
####### Expanded from @PACKAGE_INIT@ by configure_package_config_file() #######
####### Any changes to this file will be overwritten by the next CMake run ####
####### The input file was KokkosKernelsConfig.cmake.in                            ########

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

# Compute paths
get_filename_component(KokkosKernels_CMAKE_DIR "${CMAKE_CURRENT_LIST_FILE}" PATH)

include(CMakeFindDependencyMacro)



if(NOT Kokkos_FOUND)
  find_dependency(Kokkos HINTS )
endif()

include("${KokkosKernels_CMAKE_DIR}/KokkosKernelsTargets.cmake")

if(NOT TARGET KokkosKernels::all_libs)
  # CMake Error at <prefix>/lib/cmake/Kokkos/KokkosConfigCommon.cmake:10 (ADD_LIBRARY):
  #   ADD_LIBRARY cannot create ALIAS target "Kokkos::all_libs" because target
  #   "KokkosKernels::kokkoskernels" is imported but not globally visible.
  if(CMAKE_VERSION VERSION_LESS "3.18")
    set_target_properties(Kokkos::kokkoskernels PROPERTIES IMPORTED_GLOBAL ON)
  endif()
  add_library(KokkosKernels::all_libs ALIAS Kokkos::kokkoskernels)
  add_library(KokkosKernels::kokkoskernels ALIAS Kokkos::kokkoskernels)
endif()
