#!/usr/bin/env python3
# ========================================================================================
# (C) (or copyright) 2026. Triad National Security, LLC. All rights reserved.
#
# This program was produced under U.S. Government contract 89233218CNA000001 for Los
# Alamos National Laboratory (LANL), which is operated by Triad National Security, LLC
# for the U.S. Department of Energy/National Nuclear Security Administration. All rights
# in the program are reserved by Triad National Security, LLC, and the U.S. Department
# of Energy/National Nuclear Security Administration. The Government is granted for
# itself and others acting on its behalf a nonexclusive, paid-up, irrevocable worldwide
# license in this material to reproduce, prepare derivative works, distribute copies to
# the public, perform publicly and display publicly, and to permit others to do so.
# ========================================================================================
"""
RIOT radiation-flow problem: foam column with Al liner/casing and optional
V-coated glass microballoons.

Radiation transport is selectable:
    transport = "sn"         -> S_n transport (implicit Jacobi solver)
    transport = "diffusion"  -> multigroup diffusion + ionization/electron package

Mesh refinement follows the bubble choice:
    production_bubbles = True   -> adaptive refinement
    production_bubbles = False  -> static refinement
"""

from itertools import count
from pathlib import Path

import numpy as np
import riot
import singularity_eos as seos
from random_spherical_regions import generate_regions_in_cylinder


# ============================================================================
# 1. USER TOGGLES
# ============================================================================
transport = "sn"                 # "sn" or "diffusion"
production_bubbles = True       # True: real V-coated microballoons + adaptive mesh
                                 # False: larger comparable-shell bubbles + static mesh
bubble_scale_factor = 0.5        # only used when production_bubbles = False

include_casing = True
include_fillet = True
include_foam = True
include_bubbles = True

# Diffusion only: use an analytic electron EOS instead of the ion EOS
analytic_electrons = False

# Opacity source for the air trapped inside the foam ("foam_air" material)
#   "scsio2" -> foam opacity   |   "air" -> regular air opacity
foam_air_opacity_source = "scsio2"

# ---- Opacity models (per material; unlisted materials use the defaults) ----
#   absorption : "table" | "powerlaw"  (kappa = kappa0 * rho^Rhopower * T^Tpower)
#   scattering : "none" | "constant" | "table"
default_opacity_model = "table"
default_scattering_model = "constant"
default_scattering_kappa = 0.3   # cm^2/g, used by "constant" scattering

opacity_model = {
    "air":      "table",
    "foam":     "table",
    "foam_air": "table",
    "v":        "table",
    "glass":    "table",
    "al":       "table",
    "al_liner": "table",
}            
scattering_model = {
    "air":      "constant",
    "foam":     "constant",
    "foam_air": "constant",
    "v":        "constant",
    "glass":    "constant",
    "al":       "constant",
    "al_liner": "constant",
}
scattering_kappa = {
}           

if transport not in ("sn", "diffusion"):
    raise ValueError(f"transport must be 'sn' or 'diffusion', got '{transport}'")
if foam_air_opacity_source not in ("scsio2", "air"):
    raise ValueError(
        f"foam_air_opacity_source must be 'scsio2' or 'air', got '{foam_air_opacity_source}'"
    )


# ============================================================================
# 2. NUMERICS AND MESH SETTINGS
# ============================================================================
nx1, nx2, nx3 = 128, 256, 1
nx1block, nx2block, nx3block = 16, 16, 1

x0, x1 = 0.0, 0.085
y0, y1 = -0.085, 0.085
z0, z1 = 0.0, 2 * np.pi

numlev = 7
refinement = "adaptive" if production_bubbles else "static"
use_multigrid = (transport == "diffusion")   # diffusion solver needs multigrid storage

dt_init = 1e-30
tlim = 5e-8
dt_output = 2e-10
ncycle_out = 50

# Shared opacity floors
opac_temp_min = 2.0e4    # ~ minimum temperature for which opacity data is valid
opac_rho_min = 2e-3      # above background air density (1e-3)

# Diffusion-only
dt_safety_radiation = 1e-1   # temperature_fractional_change_target


# ============================================================================
# 3. GEOMETRY (cm)
# ============================================================================
um = 1.0e-4  # cm per micron

# ---- Bubbles ----
if production_bubbles:
    # Real V-coated silica microballoons
    inner_diameter = 23.0 * um
    outer_diameter = 25.0 * um
    coating_thickness = 0.1 * um
    r_core = 0.5 * inner_diameter
    r_outer = 0.5 * outer_diameter
    r_middle = r_outer - coating_thickness
    bubble_outer_radius = (r_outer, r_outer)
else:
    # Glass and vanadium thicknesses comparable
    r_core = bubble_scale_factor * 0.5 * 1.56e-2
    r_middle = bubble_scale_factor * 0.5 * 1.96e-2
    bubble_outer_radius = (bubble_scale_factor * 0.5 * 2.30e-2,
                           bubble_scale_factor * 0.5 * 2.50e-2)
    r_outer = max(bubble_outer_radius)

n_bubbles_production = 100
nonproduction_volume_fraction = 0.10   # target bubble fraction of the foam column

# ---- Foam / liner / casing ----
# Drive enters at y = y0. Below y_split an Al liner (matid 5) surrounds the foam;
# the Al casing (matid 4) surrounds everything out to casing_radius.
casing_radius = 0.07
foam_radius = 0.04625
liner_r_in = foam_radius
liner_r_out = 0.06
y_split = y0 + 0.1

# ---- Bottom collar + fillet on the casing outer wall ----
# Up to y0 + fillet_offset the casing radius is casing_radius + fillet_r; above
# that a concave arc of radius fillet_r blends back to casing_radius.
fillet_r = 0.005
fillet_offset = 0.01
fillet_y_start = y0 + fillet_offset
fillet_rc = casing_radius + fillet_r
fillet_yc = fillet_y_start + fillet_r


# ============================================================================
# 4. INITIAL STATE AND FOAM HOMOGENIZATION
# ============================================================================
eV2K = 11604.5
P0 = 1e7
T0 = 300.0
rho_air = 1e-3
rho_foam_target = 0.06   # g/cm^3, desired homogenized foam density


def eos_density_at_PT(sesame_id, P, T, filename="materials.sp5"):
    """Density of a SESAME table at (P, T).

    The method name/signature can vary between singularity-eos versions; if this
    fails, check help(seos.SpinerEOSDependsRhoT) and adjust.
    """
    eos = seos.SpinerEOSDependsRhoT(filename=filename, matid=sesame_id)
    lambda_ = np.zeros(1)
    rho, _sie = eos.DensityEnergyFromPressureTemperature(P, T, lambda_)
    return rho


def foam_volume_fractions():
    """Foam/air volume fractions giving the target bulk density in P/T equilibrium.

    Rather than forcing rho = rho_foam_target on pure foam (inconsistent with the
    EOS), use the actual EOS densities and solve for the mixture fractions.
    """
    rho_air_eos = eos_density_at_PT(5030, P0, T0)     # air
    rho_foam_eos = eos_density_at_PT(7387, P0, T0)    # SiO2 foam

    f_foam = (rho_foam_target - rho_air_eos) / (rho_foam_eos - rho_air_eos)
    f_air = 1.0 - f_foam
    if not (0.0 < f_foam < 1.0):
        raise ValueError(
            f"Computed foam volume fraction {f_foam:.6f} is outside (0, 1). "
            f"Check rho_foam_target ({rho_foam_target}) against rho_air={rho_air_eos:.6e}, "
            f"rho_foam={rho_foam_eos:.6e} at P0={P0}, T0={T0}."
        )

    m_foam = f_foam * rho_foam_eos
    m_air = f_air * rho_air_eos
    X_foam = m_foam / (m_foam + m_air)
    print(f"[EOS] rho_air(P0,T0)  = {rho_air_eos:.6e} g/cc")
    print(f"[EOS] rho_foam(P0,T0) = {rho_foam_eos:.6e} g/cc")
    print(f"[EOS] target bulk foam density = {rho_foam_target} g/cc")
    print(f"[EOS] -> foam volume fraction = {f_foam:.6f}, air volume fraction = {f_air:.6f}")
    print(f"[EOS] bulk density check = {m_foam + m_air:.6f} g/cc; "
          f"mass fraction foam = {X_foam:.6f}, air = {1 - X_foam:.6f}")
    return f_foam, f_air


f_v_foam, f_v_air = foam_volume_fractions()


# ============================================================================
# 5. RADIATION DRIVE TABLE
# ============================================================================
tev_bc_scale_times = 1.0e-8
tev_bc_scale_temps = 0.9

tev_bc_times = np.array([
    0.00E+00, 1.00E-02, 2.00E-02, 3.00E-02, 4.00E-02, 5.00E-02, 6.00E-02,
    7.00E-02, 8.00E-02, 9.00E-02, 1.00E-01, 1.10E-01, 1.20E-01, 1.30E-01,
    1.40E-01, 1.50E-01, 1.60E-01, 1.70E-01, 1.80E-01, 1.90E-01, 2.00E-01,
    2.10E-01, 2.20E-01, 2.30E-01, 2.40E-01, 2.50E-01, 2.60E-01, 2.70E-01,
    2.80E-01, 2.90E-01, 3.00E-01, 3.10E-01, 3.20E-01, 3.30E-01, 3.40E-01,
    3.50E-01, 3.60E-01, 3.70E-01, 3.80E-01, 3.90E-01, 4.00E-01, 4.10E-01,
    4.20E-01, 4.30E-01, 4.40E-01, 4.50E-01, 4.60E-01, 4.70E-01, 4.80E-01,
    4.90E-01, 5.00E-01, 5.10E-01, 5.20E-01, 5.30E-01, 5.40E-01, 5.50E-01,
    5.60E-01, 5.70E-01, 5.80E-01, 5.90E-01, 6.00E-01, 6.10E-01, 6.20E-01,
    6.30E-01, 6.40E-01, 6.50E-01, 6.60E-01, 6.70E-01, 6.80E-01, 6.90E-01,
    7.00E-01, 7.10E-01, 7.20E-01, 7.30E-01, 7.40E-01, 7.50E-01, 7.60E-01,
    0  # placeholder for the shut-off time, set to 2e-8 s below
])
tev_bc_temps = np.array([
    3.230E+01, 4.420E+01, 7.360E+01, 1.060E+02, 1.360E+02, 1.550E+02, 1.640E+02,
    1.720E+02, 1.770E+02, 1.800E+02, 1.830E+02, 1.860E+02, 1.880E+02, 1.840E+02,
    1.820E+02, 1.800E+02, 1.780E+02, 1.760E+02, 1.740E+02, 1.730E+02, 1.700E+02,
    1.690E+02, 1.670E+02, 1.630E+02, 1.550E+02, 1.470E+02, 1.410E+02, 1.350E+02,
    1.300E+02, 1.290E+02, 1.270E+02, 1.270E+02, 1.270E+02, 1.260E+02, 1.260E+02,
    1.250E+02, 1.220E+02, 1.210E+02, 1.190E+02, 1.160E+02, 1.140E+02, 1.130E+02,
    1.090E+02, 1.060E+02, 1.050E+02, 1.050E+02, 1.060E+02, 1.070E+02, 1.050E+02,
    1.000E+02, 9.750E+01, 9.630E+01, 9.610E+01, 9.380E+01, 9.630E+01, 9.610E+01,
    9.490E+01, 9.650E+01, 9.630E+01, 9.540E+01, 9.560E+01, 9.680E+01, 9.480E+01,
    9.460E+01, 9.540E+01, 9.390E+01, 9.270E+01, 9.140E+01, 9.180E+01, 9.050E+01,
    9.190E+01, 9.120E+01, 8.920E+01, 8.710E+01, 8.580E+01, 8.370E+01, 8.180E+01,
    0  # placeholder for the shut-off temperature, set to T0 below
])

drive_times_s = tev_bc_times * tev_bc_scale_times
drive_temps_K = tev_bc_temps * tev_bc_scale_temps * eV2K
drive_times_s[-1] = 2e-8   # linear ramp-down to ambient at this time
drive_temps_K[-1] = T0     # np.interp holds T0 afterwards


def Tdrive(t):
    return np.interp(t, drive_times_s, drive_temps_K)


# ============================================================================
# 6. BUBBLE PLACEMENT
# ============================================================================
def bubble_count():
    if production_bubbles:
        return n_bubbles_production
    # Mean bubble volume, averaging r^3 over the outer-radius range
    r_lo, r_hi = bubble_outer_radius
    r3_mean = (r_hi**4 - r_lo**4) / (4.0 * (r_hi - r_lo)) if r_hi > r_lo else r_lo**3
    v_bubble = (4.0 / 3.0) * np.pi * r3_mean
    v_column = np.pi * foam_radius**2 * (y1 - y0)
    return max(1, int(round(nonproduction_volume_fraction * v_column / v_bubble)))


n_bubbles = bubble_count()
print(f"[bubbles] production={production_bubbles}: {n_bubbles} bubbles in 3-D")

bubble_regions = generate_regions_in_cylinder(
    count=n_bubbles,
    z_min=y0,
    z_max=y1,
    cylinder_radius=foam_radius,
    core_radius=r_core,
    middle_radius=r_middle,
    outer_radius=bubble_outer_radius,
    dimension=3,
    non_overlapping=True,
    seed=1215,
)


# ============================================================================
# 7. INPUT-FILE BUILDERS
# ============================================================================
OPAC_FILE = "materials-opac-ngroups1.sp5"


def setup_run_and_output():
    riot.input("riot", problem="region_pgen", verbose=False)
    riot.input("parthenon/job", problem_id="output/radflow")

    # Variables common to both transport modes
    variables = [
        "c.c.bulk.rho",
        "c.c.bulk.velocity",
        "c.c.bulk.pressure",
        "c.c.bulk.total_material_energy",
        "c.c.bulk.temperature",
        "c.c.mat.volume_fraction",
    ]
    if transport == "diffusion":
        variables += [
            "c.m.ionization_zbar",
            "c.c.bulk.electron_number_density",
            "c.c.bulk.electron_internal_energy",
            "c.c.bulk.electron_pressure",
            "c.c.bulk.electron_temperature",
            "c.c.bulk.ion_shear_viscosity",
            "rmg.Eg",
        ]
    else:
        variables += ["c.c.rad.moments"]

    riot.input("parthenon/output1", variables=variables, file_type="hdf5", dt=dt_output)
    riot.input("parthenon/time", nlim=-1, tlim=tlim, integrator="rk2",
               ncycle_out=ncycle_out, dt_init=dt_init)


def setup_mesh():
    mesh_kwargs = dict(
        nghost=4, refinement=refinement, numlevel=numlev,
        nx1=nx1, x1min=x0, x1max=x1, ix1_bc="reflecting", ox1_bc="outflow",
        nx2=nx2, x2min=y0, x2max=y1, ix2_bc="outflow", ox2_bc="outflow",
        nx3=nx3, x3min=z0, x3max=z1, ix3_bc="periodic", ox3_bc="periodic",
    )
    if use_multigrid:
        mesh_kwargs["multigrid"] = True   # allocate storage for the multigrid solvers
    riot.input("parthenon/mesh", **mesh_kwargs)
    riot.input("parthenon/meshblock", nx1=nx1block, nx2=nx2block, nx3=nx3block)

    # Upsampling of initial regions for more accurate cell-level volume fractions
    riot.input("regions", nlev_min=0, nlev_max=0)


def scat_args(label, opac_material):
    """Scattering-opacity kwargs; independent of the absorption model."""
    model = scattering_model.get(label, default_scattering_model)
    if model == "none":
        return dict(opac_s="none")
    if model == "constant":
        return dict(opac_s="constant",
                    kappa_s=scattering_kappa.get(label, default_scattering_kappa))
    if model == "table":
        return dict(opac_s="table", opac_s_filename=OPAC_FILE, opac_material=opac_material)
    raise ValueError(f"Unknown scattering model '{model}' for '{label}'. "
                     f"Use 'none', 'constant' or 'table'.")


def opac_args(label, opac_material, kappa0, Rhopower=0.0, Tpower=0.0):
    """Absorption + scattering opacity kwargs for one material block."""
    model = opacity_model.get(label, default_opacity_model)
    if model == "table":
        args = dict(opac_material=opac_material, opac_a="table",
                    opac_filename=OPAC_FILE, opac_a_filename=OPAC_FILE)
    elif model == "powerlaw":
        args = dict(opac_a="powerlaw", kappa0_a=kappa0,
                    kappa_Rhopower_a=Rhopower, kappa_Tpower_a=Tpower)
    else:
        raise ValueError(f"Unknown opacity model '{model}' for '{label}'. "
                         f"Use 'table' or 'powerlaw'.")
    args.update(scat_args(label, opac_material))
    return args


def setup_materials():
    """Define materials 0-6 and their EOS blocks."""
    air_kappa = 4e6
    foam_kappa = 2e3
    solid_kappa = 1e3   # V, glass, Al, Al liner

    # foam_air takes the opacity of either the foam or the air
    if foam_air_opacity_source == "air":
        foam_air_opac_material, foam_air_kappa = "air", air_kappa
    else:
        foam_air_opac_material, foam_air_kappa = "scsio2", foam_kappa

    # (matid, label, eos block, SESAME id, opacity-table entry, power-law kappa0, (mat, bnd) levels)
    # foam_air is the air trapped INSIDE the foam: air EOS, selectable opacity.
    # Background air and bubble voids use the regular "air" material.
    materials = [
        (0, "air",      "air_eos",      5030, "air",                 air_kappa,      (0, 0)),
        (1, "foam",     "foam_eos",     7387, "scsio2",              foam_kappa,     (2, 2)),
        (2, "v",        "v_eos",        2551, "v",                   solid_kappa,    (-1, -1)),
        (3, "glass",    "glass_eos",    7387, "sio2",                solid_kappa,    (-1, -1)),
        (4, "al",       "al_eos",       3720, "al",                  solid_kappa,    (0, 0)),
        (5, "al_liner", "al_liner_eos", 3720, "al",                  solid_kappa,    (0, 0)),
        (6, "foam_air", "foam_air_eos", 5030, foam_air_opac_material, foam_air_kappa, (2, 2)),
    ]

    for matid, label, eos_name, sesame_id, opac_material, kappa0, (mat_lvl, bnd_lvl) in materials:
        kwargs = dict(label=label, eos=eos_name,
                      max_mat_level=mat_lvl, max_bnd_level=bnd_lvl)
        if transport == "diffusion":
            kwargs["electron_eos"] = "ele_eos" if analytic_electrons else eos_name
        kwargs.update(opac_args(label, opac_material, kappa0))
        riot.input(f"material{matid}", **kwargs)
        riot.input(eos_name, eos_type="SpinerEOSDependsRhoT",
                   filename="materials.sp5", sesame_id=sesame_id, zsplit=True)

        sm = scattering_model.get(label, default_scattering_model)
        sk = (f" (kappa_s = {scattering_kappa.get(label, default_scattering_kappa)} cm^2/g)"
              if sm == "constant" else "")
        print(f"[opacity] {label:9s} -> absorption: "
              f"{opacity_model.get(label, default_opacity_model):9s} scattering: {sm}{sk}")
    print(f"[opacity] foam_air opacity source = '{foam_air_opacity_source}' "
          f"(table entry: {foam_air_opac_material})")

    if transport == "diffusion":
        riot.input("ele_eos", eos_type="IdealGas", Gamma=5. / 3., Cv=6.2e7,
                   mean_atomic_mass=20, mean_atomic_number=10)


def setup_regions():
    """Initial-condition regions. Later regions override earlier ones."""
    ids = count()

    def cyl(name, matid, radius, a0, a1):
        riot.input(f"region{next(ids)}", name=name, mask_type="inside_cylinder", matid=matid,
                   x0=0.0, y0=0.0, z0=a0, x1=0.0, y1=0.0, z1=a1,
                   radius=radius, c_m_pressure=P0, c_m_temperature=T0)

    def cyl_mixture(name, matids, vfracs, radius, a0, a1):
        """Multi-material cylinder in P/T equilibrium (volume fractions from the EOS)."""
        m1, m2 = matids
        riot.input(
            f"region{next(ids)}", name=name, mask_type="inside_cylinder",
            x0=0.0, y0=0.0, z0=a0, x1=0.0, y1=0.0, z1=a1,
            radius=radius, matid=matids,
            **{
                f"c_c_mat_volume_fraction_{m1}": vfracs[0],
                f"c_c_mat_volume_fraction_{m2}": vfracs[1],
                f"c_m_pressure_{m1}": P0,
                f"c_m_pressure_{m2}": P0,
                f"c_m_temperature_{m1}": T0,
                f"c_m_temperature_{m2}": T0,
            },
        )

    def python_region(name, matid, **state):
        riot.input(f"region{next(ids)}", name=name, mask_type="python", matid=matid, **state)

    margin = 0.01   # extend past domain ends so there are no gaps

    # Background air
    riot.input(f"region{next(ids)}", name="air", mask_type="background", matid=0,
               c_m_pressure=P0, c_m_rho=rho_air)

    # Al casing (+ fillet/collar)
    if include_casing:
        cyl("al_cylinder", 4, casing_radius, y0 - margin, y1 + margin)
        if include_fillet:
            python_region("al_fillet", 4, c_m_pressure=P0, c_m_temperature=T0)

    if include_foam:
        # Al liner: solid cylinder to liner_r_out up to y_split; the foam below
        # carves out its interior, leaving liner_r_in <= r <= liner_r_out.
        cyl("al_liner_cylinder", 5, liner_r_out, y0 - margin, y_split)

        # Foam (matid 1) + foam_air (matid 6) mixture over the full height
        cyl_mixture("foam_cylinder", [1, 6], [f_v_foam, f_v_air],
                    foam_radius, y0 - margin, y1 + margin)

        # Bubbles: V outer shell, glass inner shell, air void (matid 0)
        if include_bubbles:
            python_region("v_shells", 2, c_m_pressure=P0, c_m_temperature=T0)
            python_region("glass_shells", 3, c_m_pressure=P0, c_m_temperature=T0)
            python_region("voids", 0, c_m_pressure=P0, c_m_rho=rho_air)


def setup_physics_diffusion():
    riot.input("physics", hydro=True, ionization=True,
               sparse_physics=False, multigroup_diffusion=True)
    riot.input("hydro", recon="plm", cfl=0.8, riemann="hllc", amr_interface=True)

    riot.input("ionization",
               root_tol=1e-3,
               fully_ionized=False,
               electron_ion_coupling=True,
               electron_thermal_conduction=False,
               ion_thermal_conduction=False,
               plasma_viscosity=False)
    riot.input("ionization/linear_solver_params",
               residual_tolerance=1.0e-8,
               max_coarsenings=10000,
               precondition=True,
               max_iterations=200,
               smoother="SRJ2",
               print_per_step=False)

    riot.input("diffusion",
               update_temperature=True,
               nr_tolerance=1.0e-4,
               nriter=30,
               local_nriter=0,
               print_per_nr_step=True,
               print_per_step=True,
               opacity_temp_min=opac_temp_min,
               opacity_rho_min=opac_rho_min,
               a_radiation=7.5657e-15,
               c_light=2.998e10,
               boundary_condition=[
                   "zero_flux",  # inner_x1 (r=0)
                   "zero_flux",  # outer_x1
                   "python",     # inner_x2 (drive face): radiation enters only into foam
                   "zero_flux",  # outer_x2
                   "zero_flux",  # inner_x3 (periodic, unused)
                   "zero_flux",  # outer_x3 (periodic, unused)
               ],
               boundary_T=[T0] * 6,   # unused due to python boundary conditions
               timestep_temperature_scale=opac_temp_min,
               temperature_fractional_change_target=dt_safety_radiation,
               timestep_min_temperature=opac_temp_min,
               cfl=5e5)
    riot.input("diffusion/python_bc", name="rad_bc")
    riot.input("diffusion/linear_solver_params",
               relative_residual_tolerance=1e-9,
               absolute_residual_tolerance=1e-20,
               volume_weight=True,
               max_coarsenings=100000,
               precondition=True,
               preconditioner="Multigrid",
               max_iterations=500,
               presmoother="SRJ1",
               postsmoother="SRJ2",
               block_interior_prolongation="Constant",
               print_per_step=False)


def setup_physics_sn():
    riot.input("physics", hydro=True, radiation_transport=True, sparse_physics=False)
    riot.input("hydro", recon="plm", cfl=0.8, riemann="hllc", amr_interface=True)

    riot.input("radiation_transport",
               do_jacobi=True,          # implicit; robust at large optical depth
               coupling=True,
               affect_fluid=True,       # radiation heats the matter
               nlevel=1,                # increase (2, 3) for more angles
               opac_temp_min=opac_temp_min,
               opac_rho_min=opac_rho_min,
               troot_tol=1e-4,
               troot_max_iter=50)
    riot.input("radiation_transport/jacobi",
               niter_limit=1000,
               err_thr=1.0e-3,
               dt_ratio_hyperbolic=1.0e4,
               verbose=1,
               nreduce_limit=8, reduce_factor=2, ndiverge_limit=20)
    riot.input("radiation_transport/init", initialization="thermal")   # E_r = a T0^4
    riot.input("radiation_transport/drive",
               ix2_bc="drive",
               drive_source="python",
               force_upwind_flux_bc=True)
    riot.input("radiation_transport/python_bc", name="rad_bc")


def make_input():
    print(f"[config] transport={transport}, production_bubbles={production_bubbles}, "
          f"refinement={refinement}, multigrid={use_multigrid}")
    setup_run_and_output()
    setup_mesh()
    setup_materials()
    setup_regions()
    if transport == "diffusion":
        setup_physics_diffusion()
    else:
        setup_physics_sn()


# ============================================================================
# 8. PYTHON REGION AND BOUNDARY CLASSES (looked up by name from the input file)
# ============================================================================
class al_fillet:
    def mask(self, pos):
        # Cylinder axis is z; radius is sqrt(x^2 + y^2)
        r = np.sqrt(pos[:, 0] ** 2 + pos[:, 1] ** 2)
        y = pos[:, 2]

        in_r = (r >= casing_radius) & (r <= fillet_rc)
        collar = in_r & (y >= y0) & (y <= fillet_y_start)

        in_arc_box = in_r & (y > fillet_y_start) & (y <= fillet_yc)
        outside_arc = (r - fillet_rc) ** 2 + (y - fillet_yc) ** 2 >= fillet_r ** 2
        return collar | (in_arc_box & outside_arc)


class v_shells:
    def mask(self, pos):
        return bubble_regions.classify_outer_shell_points(pos)


class glass_shells:
    def mask(self, pos):
        return bubble_regions.classify_inner_shell_points(pos)


class voids:
    def mask(self, pos):
        return bubble_regions.classify_center_points(pos)


class rad_bc:
    """Foam-only radiation drive on the inner x2 face (y = y0)."""

    def rad_temperature(self, t, face, pos, T):
        if face == "inner_x2":
            # Radial (x1) ghost-cell coordinate: self.x if provided, else column 0
            r = pos[:, getattr(self, "x", 0)]
            # Smooth transition from Tdrive (inside foam) to T0 (liner/casing/air)
            tanh_steepness = 100
            f = 0.5 + 0.5 * np.tanh((r / foam_radius - 1.0) * tanh_steepness)
            T[:] = T0 + (Tdrive(t) - T0) * (1.0 - f)
        else:
            T[:] = T0


# ============================================================================
# 9. OPTIONAL DIAGNOSTIC
# ============================================================================
def plot_drive(fname="output/drive_profile.png"):
    """Plot T(t) at the drive face and the radial profile at y = y0."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    Path(fname).parent.mkdir(parents=True, exist_ok=True)
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.5))

    # (1) Drive temperature vs time
    ax = axs[0]
    t = np.linspace(0.0, tlim, 2000)
    ax.plot(t * 1e9, Tdrive(t) / eV2K, lw=2, label="Tdrive(t) (interpolated)")
    ax.plot(drive_times_s * 1e9, drive_temps_K / eV2K, "o", ms=3, alpha=0.6, label="table points")
    ax.axhline(T0 / eV2K, color="gray", ls="--", lw=1, label=f"T0 = {T0:g} K")
    ax.set_xlabel("time [ns]")
    ax.set_ylabel("drive temperature [eV]")
    ax.set_title("Drive temperature vs time")
    ax.grid(alpha=0.3)
    ax.legend()

    # (2) Radial profile at the drive face
    ax = axs[1]
    r = np.linspace(0.0, x1, 1000)
    pos = np.zeros((r.size, 3))
    pos[:, 0] = r
    bc = rad_bc()
    for tt in [0.0, 2e-9, 5e-9, 1e-8, 2e-8]:
        Tface = np.empty_like(r)
        bc.rad_temperature(tt, "inner_x2", pos, Tface)
        ax.plot(r * 10, Tface / eV2K, label=f"t = {tt * 1e9:g} ns")
    ax.axvline(foam_radius * 10, color="k", ls=":", lw=1, label="foam radius")
    ax.set_xlabel("r [mm]")
    ax.set_ylabel("drive temperature [eV]")
    ax.set_title("Radial drive profile at y = y0")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)

    fig.tight_layout()
    fig.savefig(fname, dpi=150)
    plt.close(fig)
    print(f"[drive] wrote {fname}")


if __name__ == "__main__":
    make_input()
    riot.input.generate_input()
    # plot_drive()