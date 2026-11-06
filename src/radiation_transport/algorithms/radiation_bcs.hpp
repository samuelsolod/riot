//========================================================================================
// (C) (or copyright) 2026. Triad National Security, LLC. All rights reserved.
//
// This program was produced under U.S. Government contract 89233218CNA000001 for Los
// Alamos National Laboratory (LANL), which is operated by Triad National Security, LLC
// for the U.S. Department of Energy/National Nuclear Security Administration. All rights
// in the program are reserved by Triad National Security, LLC, and the U.S. Department
// of Energy/National Nuclear Security Administration. The Government is granted for
// itself and others acting on its behalf a nonexclusive, paid-up, irrevocable worldwide
// license in this material to reproduce, prepare derivative works, distribute copies to
// the public, perform publicly and display publicly, and to permit others to do so.
//========================================================================================
#ifndef RADIATION_ALGORITHMS_RADIATION_BCS_HPP_
#define RADIATION_ALGORITHMS_RADIATION_BCS_HPP_
// This file was made in part with generative AI.

// C++ headers
#include <algorithm>
#include <array>
#include <limits>
#include <memory>
#include <string>
#include <vector>

// Parthenon headers
#include <bvals/boundary_conditions_generic.hpp>
#include <parthenon/package.hpp>
#include <utils/string_utils.hpp>

// Riot headers
#include "radiation_transport/angular_grids/geodesic_grid.hpp"
#include "radiation_transport/angular_grids/latlon_grid.hpp"
#include "radiation_transport/transport_utils/transport_utils.hpp"
#include "riot_utils/riot_utils.hpp"
#include "riot_utils/table_utils.hpp"
#ifdef RIOT_ENABLE_PYTHON
#include "riot_pgen/region_python.hpp"
#endif

using namespace parthenon::package::prelude;

// Handle to the python object that supplies the drive temperature
#ifdef RIOT_ENABLE_PYTHON
using drive_py_t = std::shared_ptr<python_region_t>;
#else
using drive_py_t = std::nullptr_t;
#endif

namespace Drive {
using Side = parthenon::BoundaryFunction::BCSide;

//----------------------------------------------------------------------------------------
//! \fn constexpr IndexDomain BndryDomain
//! \brief Maps (DIR, SIDE) to the corresponding ghost-zone IndexDomain
template <parthenon::CoordinateDirection DIR, parthenon::BoundaryFunction::BCSide SIDE>
inline constexpr IndexDomain BndryDomain() {
  constexpr bool inner = (SIDE == parthenon::BoundaryFunction::BCSide::Inner);
  if constexpr (DIR == parthenon::X1DIR) {
    return inner ? IndexDomain::inner_x1 : IndexDomain::outer_x1;
  } else if constexpr (DIR == parthenon::X2DIR) {
    return inner ? IndexDomain::inner_x2 : IndexDomain::outer_x2;
  } else {
    return inner ? IndexDomain::inner_x3 : IndexDomain::outer_x3;
  }
}

//----------------------------------------------------------------------------------------
//! \fn KOKKOS_FORCEINLINE_FUNCTION void RefIdx
//! \brief Returns the (k,j,i) index triple with the DIR-normal component replaced by the
//! interior reference index `ref`, i.e. the cell copied/reflected into the ghost zone
template <parthenon::CoordinateDirection DIR>
KOKKOS_FORCEINLINE_FUNCTION void RefIdx(const int k, const int j, const int i,
                                        const int ref, int &kr, int &jr, int &ir) {
  kr = (DIR == parthenon::X3DIR) ? ref : k;
  jr = (DIR == parthenon::X2DIR) ? ref : j;
  ir = (DIR == parthenon::X1DIR) ? ref : i;
}

//----------------------------------------------------------------------------------------
//! \fn IndexRange InteriorBounds
//! \brief Returns the interior IndexRange along the DIR-normal axis
template <parthenon::CoordinateDirection DIR>
inline const IndexRange InteriorBounds(const parthenon::IndexShape &bounds) {
  if constexpr (DIR == parthenon::X1DIR) {
    return bounds.GetBoundsI(IndexDomain::interior);
  } else if constexpr (DIR == parthenon::X2DIR) {
    return bounds.GetBoundsJ(IndexDomain::interior);
  } else {
    return bounds.GetBoundsK(IndexDomain::interior);
  }
}

#ifdef RIOT_ENABLE_PYTHON
//----------------------------------------------------------------------------------------
//! \struct DriveTemperature
//! \brief Per-ghost-zone radiation temperature on the device, plus the index offsets of
//! the first ghost zone so that T(k - ks, j - js, i - is) is the zone value
struct DriveTemperature {
  parthenon::ParArray3D<Real> T;
  int ks, js, is;
};

//----------------------------------------------------------------------------------------
//! \fn DriveTemperature GetDriveTemperature
//! \brief Calls the python method
//!   rad_temperature(self, time, face, pos, T)
//! on the host, where face is e.g. "inner_x2", pos is an (n, 3) array of ghost-zone
//! centers (x1, x2, x3) and T is an (n,) array filled in place [K]. The result is copied
//! to the device.
template <parthenon::CoordinateDirection DIR, parthenon::BoundaryFunction::BCSide SIDE>
DriveTemperature GetDriveTemperature(const drive_py_t &py, const std::string &face,
                                     parthenon::MeshBlock *pmb, bool coarse, Real time) {
  using namespace parthenon;
  using TE = TopologicalElement;

  // Same index ranges as pmb->par_for_bndry(..., domain, TE::CC, coarse, false, ...)
  constexpr IndexDomain domain = BndryDomain<DIR, SIDE>();
  const auto &bounds = coarse ? pmb->c_cellbounds : pmb->cellbounds;
  const auto ib = bounds.GetBoundsI(domain, TE::CC);
  const auto jb = bounds.GetBoundsJ(domain, TE::CC);
  const auto kb = bounds.GetBoundsK(domain, TE::CC);
  const auto coords = coarse ? pmb->pmr->GetCoarseCoords() : pmb->coords;

  const int nk = kb.e - kb.s + 1;
  const int nj = jb.e - jb.s + 1;
  const int ni = ib.e - ib.s + 1;
  DriveTemperature out{ParArray3D<Real>("drive python T", nk, nj, ni), kb.s, jb.s, ib.s};
  auto T_h = Kokkos::create_mirror_view(out.T);
  Kokkos::View<Real ****, LayoutWrapper, HostMemSpace> pos_h("drive python pos", nk, nj,
                                                             ni, 3);
  parthenon::seq_for(kb, jb, ib, [&](const int k, const int j, const int i) {
    pos_h(k - kb.s, j - jb.s, i - ib.s, 0) = coords.Xc<X1DIR>(i);
    pos_h(k - kb.s, j - jb.s, i - ib.s, 1) = coords.Xc<X2DIR>(j);
    pos_h(k - kb.s, j - jb.s, i - ib.s, 2) = coords.Xc<X3DIR>(k);
  });

  // Both host views are contiguous (LayoutRight), so python sees them as flat arrays
  const std::size_t n = static_cast<std::size_t>(nk) * nj * ni;
  py->py_obj.call_method<void>(
      "rad_temperature", time, face,
      pcall::ArrayViewND<const Real>{pos_h.data(), {n, 3}, false},
      pcall::ArrayViewND<Real>{T_h.data(), {n}, true});
  Kokkos::deep_copy(out.T, T_h);
  return out;
}
#endif // RIOT_ENABLE_PYTHON

//----------------------------------------------------------------------------------------
//! \fn template <CoordinateDirection DIR, BCSide SIDE> void DriveBCImpl
//! \brief Unified drive boundary condition for all six faces
template <parthenon::CoordinateDirection DIR, parthenon::BoundaryFunction::BCSide SIDE>
inline void DriveBCImpl(std::shared_ptr<MeshBlockData<Real>> &mbd, bool coarse,
                        const drive_py_t &py, const std::string &face) {
  using parthenon::MakePackDescriptor;
  namespace ccrad = cell_variables::cell_averaged::rad;
  auto pmb = mbd->GetBlockPointer();
  const auto nb = IndexRange{0, 0};

  // Indexing for outflow: reference the first (inner) or last (outer) interior cell
  constexpr bool inner = (SIDE == parthenon::BoundaryFunction::BCSide::Inner);
  const auto &bounds = coarse ? pmb->c_cellbounds : pmb->cellbounds;
  const auto range = InteriorBounds<DIR>(bounds);
  const int ref = inner ? range.s : range.e;
  constexpr IndexDomain domain = BndryDomain<DIR, SIDE>();

  // Radiation package
  const auto rad_pkg = GetRadPackage(pmb->packages);
  const int ngroups = rad_pkg->Param<int>("ngroups");
  const int nangles = rad_pkg->Param<int>("nangles");

  // Intensity
  static auto descr = GetBoundaryPackDescriptorMap<ccrad::intensity>(mbd);
  auto vr = descr[coarse].GetPack(mbd.get());
  if (vr.GetMaxNumberOfVars() > 0) {
    using RiotTables::Uniform1D;
    const bool from_table = rad_pkg->Param<bool>("drive_from_table");
    const bool from_python = rad_pkg->Param<bool>("drive_from_python");
    auto unit_utils = rad_pkg->Param<UnitUtils>("unit_utils");
    Real trad = 0.0, time_bc = 0.0;
    ParArray1D<Real> fbnd, tmin, tmax;
    ParArray1D<Uniform1D> eg;
    // Python mode: per-ghost-zone temperature and the offset of the first ghost zone
    parthenon::ParArray3D<Real> Tb;
    int tks = 0, tjs = 0, tis = 0;
    if (from_table) {
      eg = rad_pkg->Param<ParArray1D<Uniform1D>>("d.drive_eg");
      tmin = rad_pkg->Param<ParArray1D<Real>>("d.drive_eg_tmin");
      tmax = rad_pkg->Param<ParArray1D<Real>>("d.drive_eg_tmax");
      time_bc = rad_pkg->Param<Real>("time");
    } else {
      fbnd = *(rad_pkg->MutableParam<ParArray1D<Real>>("fbnd_d"));
      if (from_python) {
#ifdef RIOT_ENABLE_PYTHON
        time_bc = rad_pkg->Param<Real>("time");
        auto d = GetDriveTemperature<DIR, SIDE>(py, face, pmb, coarse, time_bc);
        Tb = d.T;
        tks = d.ks;
        tjs = d.js;
        tis = d.is;
#endif
      } else {
        trad = rad_pkg->Param<Real>("drive_trad");
      }
    }
    pmb->par_for_bndry(
        "DriveBC-intensity", nb, domain, parthenon::TopologicalElement::CC, coarse, false,
        KOKKOS_LAMBDA(const int &l, const int &k, const int &j, const int &i) {
          int kr, jr, ir;
          RefIdx<DIR>(k, j, i, ref, kr, jr, ir);
          const Real tcell = from_python ? Tb(k - tks, j - tjs, i - tis) : trad;
          for (int gg = 0; gg < ngroups; ++gg) {
            const Real ee =
                from_table ? std::max(eg(gg).interpToReal(std::max(
                                          tmin(gg), std::min(tmax(gg), time_bc))),
                                      0.0)
                           : ((tcell > 0)
                                  ? Emissivity(gg, tcell, fbnd, ngroups, unit_utils)
                                  : 0.0);
            for (int aa = 0; aa < nangles; ++aa) {
              vr(0, ccrad::intensity(GAI(nangles, gg, aa)), k, j, i) =
                  std::max(ee, vr(0, ccrad::intensity(GAI(nangles, gg, aa)), kr, jr, ir));
            }
          }
        });
  }

  // Opacity: by default zero at the boundary to force a purely upwind flux (even when
  // radiation_transport/beta > 0)
  static auto desco = GetBoundaryPackDescriptorMap<ccrad::aa, ccrad::ss>(mbd);
  auto vo = desco[coarse].GetPack(mbd.get());
  if (vo.GetMaxNumberOfVars() > 0) {
    const bool dp_force_upwind_flux_bc =
        rad_pkg->Param<bool>("drive_force_upwind_flux_bc");
    const Real copy = (dp_force_upwind_flux_bc) ? 0.0 : 1.0;
    pmb->par_for_bndry(
        "DriveBC-opacity", nb, domain, parthenon::TopologicalElement::CC, coarse, false,
        KOKKOS_LAMBDA(const int &l, const int &k, const int &j, const int &i) {
          int kr, jr, ir;
          RefIdx<DIR>(k, j, i, ref, kr, jr, ir);
          for (int gg = 0; gg < ngroups; ++gg) {
            vo(0, ccrad::aa(gg), k, j, i) = copy * vo(0, ccrad::aa(gg), kr, jr, ir);
            vo(0, ccrad::ss(gg), k, j, i) = copy * vo(0, ccrad::ss(gg), kr, jr, ir);
          }
        });
  }
}

template <parthenon::CoordinateDirection DIR, parthenon::BoundaryFunction::BCSide SIDE>
inline auto DriveBC(drive_py_t py, std::string face) {
  return [py, face](std::shared_ptr<MeshBlockData<Real>> &mbd, bool coarse) -> void {
    using namespace parthenon;
    using namespace parthenon::BoundaryFunction;
    DriveBCImpl<DIR, SIDE>(mbd, coarse, py, face);
  };
}

} // namespace Drive

//----------------------------------------------------------------------------------------
// Shared initialization for the radiation transport BCs (default, drive).
//----------------------------------------------------------------------------------------
namespace RadiationBC {

// The input blocks from which the shared radiation BCs are read.
constexpr char drive_block[] = "radiation_transport/drive";
// Input block defining the python class used when drive_source = "python".
constexpr char drive_python_block[] = "radiation_transport/python_bc";

// Individual shared BC options.
namespace BCOption {

//----------------------------------------------------------------------------------------
//! \fn void AddDriveParams
//! \brief Read the drive-BC parameters.  Three sources are supported:
//!  - "constant": drives a uniform radiation temperature trad_bc
//!  - "table": reads a time series of group radiation energy densities E_g(t)
//!    [erg/cm^3] from drive_filename (col 0 = time [s], cols 1..ngroups = E_g), one
//!    Uniform1D per group evaluated at the solver's current update time
//!  - "python": radiation temperature [K] per ghost zone from the python method
//!    rad_temperature(self, time, face, pos, T) of the class in
//!    <radiation_transport/python_bc>
inline void AddDriveParams(StateDescriptor *rad_pkg, ParameterInput *pin,
                           Params &params) {
  const std::string source = pin->GetOrAddString(
      drive_block, "drive_source", "constant",
      "Drive BC source: \"constant\" (trad_bc), \"table\" (E_g time series from "
      "drive_filename) or \"python\" (rad_temperature method of a python class)");
  const bool from_table = (source == "table");
  const bool from_python = (source == "python");
  PARTHENON_REQUIRE(from_table || from_python || source == "constant",
                    "radiation_transport/drive/drive_source must be "
                    "\"constant\", \"table\" or \"python\".");
  params.Add("drive_from_table", from_table);
  params.Add("drive_from_python", from_python);

  if (from_table) {
    const int ngroups = rad_pkg->Param<int>("ngroups");
    const std::string fname = pin->GetString(
        drive_block, "drive_filename",
        "Path to the drive E_g(t) table (col 0 = time [s], cols 1..ngroups = group "
        "radiation energy density [erg/cm^3]).");
    const auto table = parthenon::string_utils::ParseAsciiTable<Real>(fname);
    const int nrows = table.extent(0);
    const int ncols = table.extent(1);
    PARTHENON_REQUIRE(ncols == ngroups + 1,
                      "Drive table must have ngroups+1 columns (time + one per group).");

    std::vector<RiotTables::Uniform1D> eg_d_vec;
    std::vector<Real> tmin_h, tmax_h;
    parthenon::HostArray2D<Real> col("drive_eg_col", nrows, 2);
    for (int g = 0; g < ngroups; ++g) {
      for (int r = 0; r < nrows; ++r) {
        col(r, 0) = table(r, 0);
        col(r, 1) = table(r, g + 1);
      }
      auto eg_h = RiotTables::UniformlyResampleTimeSeries(col);
      eg_d_vec.push_back(eg_h.getOnDevice());
      tmin_h.push_back(eg_h.range(0).min());
      tmax_h.push_back(eg_h.range(0).max());
    }
    params.Add("d.drive_eg", RiotUtils::VectorToDevice(eg_d_vec, "drive_eg"));
    params.Add("d.drive_eg_tmin", RiotUtils::VectorToDevice(tmin_h, "drive_eg_tmin"));
    params.Add("d.drive_eg_tmax", RiotUtils::VectorToDevice(tmax_h, "drive_eg_tmax"));
  } else if (!from_python) {
    // Constant mode only. Python mode gets its temperature from the python class.
    params.Add("drive_trad",
               pin->GetOrAddReal(drive_block, "trad_bc", 0.0,
                                 "Uniform radiation temperature (in K) for the drive "
                                 "boundary condition (constant mode)."));
  }

  params.Add("drive_force_upwind_flux_bc",
             pin->GetOrAddBoolean(
                 drive_block, "force_upwind_flux_bc", true,
                 "Zero the boundary opacity to force a purely upwind flux (even when "
                 "radiation_transport/beta > 0) for the drive boundary condition; if "
                 "false, copy the interior opacity instead"));
}

} // namespace BCOption

//----------------------------------------------------------------------------------------
//! \struct Helper struct for boundary enrollment
//! \brief
struct BoundarySpec {
  const char *name;
  BoundaryFace face;
};

//----------------------------------------------------------------------------------------
//! \fn EnrollRadiationBC
//! \brief
inline void EnrollRadiationBC(StateDescriptor *rad_pkg, ParameterInput *pin,
                              Params &params) {
  using Side = parthenon::BoundaryFunction::BCSide;
  using RadBFunc_t = typename std::remove_reference_t<
      decltype(rad_pkg->UserBoundaryFunctions[BoundaryFace::inner_x1])>::value_type;

  // Boundary faces
  const std::array<BoundarySpec, 6> bndry_faces = {{
      {"ix1_bc", BoundaryFace::inner_x1},
      {"ox1_bc", BoundaryFace::outer_x1},
      {"ix2_bc", BoundaryFace::inner_x2},
      {"ox2_bc", BoundaryFace::outer_x2},
      {"ix3_bc", BoundaryFace::inner_x3},
      {"ox3_bc", BoundaryFace::outer_x3},
  }};

  // Which faces use the drive BC?
  std::array<bool, 6> is_drive{};
  bool drive_bc_enrolled = false;
  for (int i = 0; i < static_cast<int>(bndry_faces.size()); ++i) {
    is_drive[i] =
        (pin->GetOrAddString(drive_block, bndry_faces[i].name, "default") == "drive");
    drive_bc_enrolled = drive_bc_enrolled || is_drive[i];
  }
  if (!drive_bc_enrolled) return;

  // Python object supplying the drive temperature (only for drive_source = "python").
  // It is created once here and shared by the BC functions of all drive faces.
  drive_py_t py{};
  const std::string source = pin->GetOrAddString(
      drive_block, "drive_source", "constant",
      "Drive BC source: \"constant\" (trad_bc), \"table\" (E_g time series from "
      "drive_filename) or \"python\" (rad_temperature method of a python class)");
  if (source == "python") {
#ifdef RIOT_ENABLE_PYTHON
    // The python class is specified by name (and optionally file) in the
    // <radiation_transport/python_bc> block. Parameters in <name/params> are set as
    // attributes.
    PARTHENON_REQUIRE(pin->DoesParameterExist(drive_python_block, "name"),
                      "drive_source=python requires a <radiation_transport/python_bc> "
                      "block with name set to the python drive class.");
    py = std::make_shared<python_region_t>(pin, drive_python_block);
    PARTHENON_REQUIRE(py->IsInitialized(),
                      "Could not construct python class for the radiation drive. Set "
                      "file in <radiation_transport/python_bc> to the file defining the "
                      "class.");
    PARTHENON_REQUIRE(py->py_obj.exists("rad_temperature"),
                      "Python radiation drive class needs a rad_temperature method.");
#else
    PARTHENON_FAIL("drive_source=python requires RIOT_ENABLE_PYTHON.");
#endif
  }

  // Drive BC functions
  const std::array<RadBFunc_t, 6> drive_bcs = {
      Drive::DriveBC<X1DIR, Side::Inner>(py, "inner_x1"),
      Drive::DriveBC<X1DIR, Side::Outer>(py, "outer_x1"),
      Drive::DriveBC<X2DIR, Side::Inner>(py, "inner_x2"),
      Drive::DriveBC<X2DIR, Side::Outer>(py, "outer_x2"),
      Drive::DriveBC<X3DIR, Side::Inner>(py, "inner_x3"),
      Drive::DriveBC<X3DIR, Side::Outer>(py, "outer_x3")};

  // Enroll custom BC
  for (int i = 0; i < static_cast<int>(bndry_faces.size()); ++i) {
    if (is_drive[i]) {
      rad_pkg->UserBoundaryFunctions[bndry_faces[i].face].push_back(drive_bcs[i]);
    }
  }

  // Enroll custom BC params
  BCOption::AddDriveParams(rad_pkg, pin, params);
}

} // namespace RadiationBC

#endif // RADIATION_ALGORITHMS_RADIATION_BCS_HPP_
