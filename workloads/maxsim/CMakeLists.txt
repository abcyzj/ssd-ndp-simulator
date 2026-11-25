if(NDPSIM_SANITIZE)
    return()
endif()

# Bind the element to the fork commit and actual in-tree sources it is built with.
find_package(Git REQUIRED)
execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${MQSIM_SOURCE}" rev-parse HEAD
    OUTPUT_VARIABLE MAXSIM_MQSIM_COMMIT OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
file(GLOB_RECURSE maxsim_mqsim_files LIST_DIRECTORIES false CONFIGURE_DEPENDS
    RELATIVE "${MQSIM_SOURCE}" "${MQSIM_SOURCE}/src/*")
set(maxsim_mqsim_inventory "")
foreach(name IN LISTS maxsim_mqsim_files)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${MQSIM_SOURCE}/${name}")
    file(SHA256 "${MQSIM_SOURCE}/${name}" digest)
    string(APPEND maxsim_mqsim_inventory "${name}:${digest}\n")
endforeach()
string(SHA256 MAXSIM_MQSIM_SOURCE_SHA256 "${maxsim_mqsim_inventory}")
configure_file(build_identity.h.in build_identity.h @ONLY)

add_library(ndpsim_maxsim MODULE sst.cc run.cc nvme.cc local_read_probe.cc)
target_include_directories(ndpsim_maxsim PRIVATE "${CMAKE_CURRENT_BINARY_DIR}"
    "${PROJECT_SOURCE_DIR}/third_party/sst-core/external")
target_link_libraries(ndpsim_maxsim PRIVATE ndpsim_dag ndpsim_controller_timing ndpsim_backend SST::SSTCore)
if(APPLE)
    target_link_options(ndpsim_maxsim PRIVATE "-undefined" "dynamic_lookup")
endif()
if(BUILD_TESTING)
    add_test(NAME maxsim_e0_execution COMMAND "${Python3_EXECUTABLE}"
        "${CMAKE_CURRENT_SOURCE_DIR}/test_execution.py" "${PROJECT_BINARY_DIR}")
    set_tests_properties(maxsim_e0_execution PROPERTIES TIMEOUT 300)
endif()
