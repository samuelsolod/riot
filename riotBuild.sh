#!/usr/bin/env bash
set -euo pipefail

source /vast/home/ssolod/spack/share/spack/setup-env.sh
spack env activate riot_env

VIEW="$(spack location -e riot_env)/.spack-env/view"

export PATH="$VIEW/bin:$PATH"
export CMAKE_PREFIX_PATH="$VIEW"
export LD_LIBRARY_PATH="$VIEW/lib:${LD_LIBRARY_PATH:-}"   # not DYLD_ on Linux

# ---------------------------------------------------------------------------
# Compiler selection
# Kokkos 5.x needs C++20 => GCC >= 10.4. The system GCC (8.5) is too old.
# Optionally load a newer compiler first, e.g. uncomment ONE of:
#   module load gcc/12.2.0
#   spack load gcc@12
#   source scl_source enable gcc-toolset-12
# ---------------------------------------------------------------------------
# Use the MPI wrappers so the compiler matches the MPI/HDF5 in the spack view
export CC="${CC:-$(which mpicc)}"
export CXX="${CXX:-$(which mpicxx)}"

echo "cmake:  $(which cmake)"
echo "mpicc:$CC"
echo "mpicxx: $CXX"
echo "h5cc:$(which h5cc)"

# Show and verify the underlying compiler version
CXX_VERSION="$($CXX -dumpfullversion -dumpversion)"
echo "C++ compiler version: $CXX_VERSION"
if [ "$(printf '%s\n' "10.4.0" "$CXX_VERSION" | sort -V | head -n1)" != "10.4.0" ]; then
  echo "ERROR: C++ compiler$CXX_VERSION is too old for Kokkos (need >= 10.4.0)." >&2
  echo "Load a newer GCC (module load / spack load / scl enable) or rebuild the" >&2
  echo "spack env with a newer compiler, then rerun." >&2
  exit 1
fi

BUILD_DIR="$PWD/build"

# CMake caches the compiler; remove a stale cache that picked GCC 8.5
if [ -f "$BUILD_DIR/CMakeCache.txt" ] && \
   grep -q "CMAKE_CXX_COMPILER_VERSION\|/usr/bin/c++" "$BUILD_DIR/CMakeCache.txt" 2>/dev/null; then
  if grep -q "^CMAKE_CXX_COMPILER:.*=/usr/bin/c++" "$BUILD_DIR/CMakeCache.txt"; then
    echo "Removing stale CMake cache (built with system compiler)"
    rm -rf "$BUILD_DIR"
  fi
fi
mkdir -p "$BUILD_DIR"

cmake -S . -B "$BUILD_DIR" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER="$CC" \
  -DCMAKE_CXX_COMPILER="$CXX" \
  -DCMAKE_PREFIX_PATH="$VIEW" \
  -DHDF5_ROOT="$VIEW" \
  -DHDF5_IS_PARALLEL=ON \
  -DHDF5_PREFER_PARALLEL=ON \
  -DPython_ROOT_DIR="$VIEW" \
  -DPython_EXECUTABLE="$VIEW/bin/python3" \
  -DPython_FIND_STRATEGY=LOCATION \
  -DCMAKE_INSTALL_PREFIX="$VIEW" \
  -DPARTHENON_COORDINATES=UniformCylindrical \
  -DRIOT_ENABLE_HDF5=ON \
  -DRIOT_ENABLE_MPI=ON \
  -DRIOT_BUILD_SINGULARITY=ON \
  -DRIOT_BUILD_PYTHON=ON

cmake --build "$BUILD_DIR" --parallel "${SLURM_CPUS_ON_NODE:-8}"
# cmake --install "$BUILD_DIR"   # only if you want to install into the spack view