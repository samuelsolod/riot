#!/usr/bin/env bash
set -euo pipefail

BUILD_DIR='/Users/ssolod/Desktop/codes/riot/build'

if [ ! -d "$BUILD_DIR" ]; then
    mkdir -p "$BUILD_DIR" 
    echo "Directory created."
else 
    echo "Directory already exists."
fi

spack env activate riot-env

VIEW="$(spack location -e riot-env)/.spack-env/view"

export PATH="$VIEW/bin:$PATH"
export CMAKE_PREFIX_PATH="$VIEW"
export DYLD_LIBRARY_PATH="$VIEW/lib:${DYLD_LIBRARY_PATH:-}"

echo "Using cmake:$(which cmake)"
echo "Using mpicc:  $(which mpicc)"
echo "Using h5dump:$(which h5dump)"

cmake -S . -B build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$VIEW" \
  -DHDF5_ROOT="$VIEW" \
  -DHDF5_IS_PARALLEL=ON \
  -DHDF5_PREFER_PARALLEL=ON \
  -DPython_ROOT_DIR="$VIEW" \
  -DPython_EXECUTABLE="$VIEW/bin/python3.11" \
  -DPython_FIND_STRATEGY=LOCATION \
  -DCMAKE_INSTALL_PREFIX="$VIEW" \
  -DPARTHENON_COORDINATES=UniformCylindrical \
  -DRIOT_ENABLE_HDF5=ON \
  -DRIOT_ENABLE_MPI=ON \
  -DRIOT_BUILD_SINGULARITY=ON \
  -DRIOT_BUILD_PYTHON=ON

cmake --build build --parallel 6

cmake --install build

