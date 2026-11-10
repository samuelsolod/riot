#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "Parthenon::parthenon" for configuration "Release"
set_property(TARGET Parthenon::parthenon APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(Parthenon::parthenon PROPERTIES
  IMPORTED_LINK_INTERFACE_LANGUAGES_RELEASE "CXX"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libparthenon.a"
  )

list(APPEND _cmake_import_check_targets Parthenon::parthenon )
list(APPEND _cmake_import_check_files_for_Parthenon::parthenon "${_IMPORT_PREFIX}/lib/libparthenon.a" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
