
####### Expanded from @PACKAGE_INIT@ by configure_package_config_file() #######
####### Any changes to this file will be overwritten by the next CMake run ####
####### The input file was spinerConfig.cmake.in                            ########

get_filename_component(PACKAGE_PREFIX_DIR "${CMAKE_CURRENT_LIST_DIR}/../../../" ABSOLUTE)

macro(set_and_check _var _file)
  set(${_var} "${_file}")
  if(NOT EXISTS "${_file}")
    message(FATAL_ERROR "File or directory ${_file} referenced by variable ${_var} does not exist !")
  endif()
endmacro()

macro(check_required_components _NAME)
  foreach(comp ${${_NAME}_FIND_COMPONENTS})
    if(NOT ${_NAME}_${comp}_FOUND)
      if(${_NAME}_FIND_REQUIRED_${comp})
        set(${_NAME}_FOUND FALSE)
      endif()
    endif()
  endforeach()
endmacro()

####################################################################################


set(SPINER_BUILD_TESTS "OFF")
set(SPINER_DEP_TARGETS "ports-of-call::ports-of-call;hdf5::hdf5;hdf5::hdf5_hl")
set(SPINER_TEST_USE_KOKKOS "OFF")
set(SPINER_TEST_USE_KOKKOS_AVAILABLE "0")
set(SPINER_TEST_USE_KOKKOS_CUDA "ON")
set(SPINER_TEST_USE_KOKKOS_CUDA_AVAILABLE "0")
set(SPINER_USE_HDF "ON")
set(SPINER_VERSION "1.6.4")

include(CMakeFindDependencyMacro)

find_dependency(ports-of-call)

if(ON)
  find_dependency(HDF5 COMPONENTS C HL)
  if(HDF5_IS_PARALLEL)
    find_dependency(MPI COMPONENTS CXX)
  endif()
endif()

include("${CMAKE_CURRENT_LIST_DIR}/spinerTargets.cmake")

check_required_components(spiner)

# © 2021. Triad National Security, LLC. All rights reserved.  This program was
# produced under U.S. Government contract 89233218CNA000001 for Los Alamos
# National Laboratory (LANL), which is operated by Triad National Security, LLC
# for the U.S.  Department of Energy/National Nuclear Security Administration.
# All rights in the program are reserved by Triad National Security, LLC, and
# the U.S. Department of Energy/National Nuclear Security Administration. The
# Government is granted for itself and others acting on its behalf a
# nonexclusive, paid-up, irrevocable worldwide license in this material to
# reproduce, prepare derivative works, distribute copies to the public, perform
# publicly and display publicly, and to permit others to do so.
