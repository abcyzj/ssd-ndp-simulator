cmake_minimum_required(VERSION 3.22)

# CMake 4 no longer selects a macOS SDK implicitly. Keep a plain configure
# usable with Command Line Tools, while honoring explicit toolchain settings.
if(CMAKE_HOST_APPLE AND NOT DEFINED CMAKE_OSX_SYSROOT AND NOT DEFINED ENV{SDKROOT})
    execute_process(COMMAND xcrun --show-sdk-path OUTPUT_VARIABLE _ndpsim_sdk
        OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
    set(CMAKE_OSX_SYSROOT "${_ndpsim_sdk}" CACHE PATH "macOS SDK")
endif()
project(SSD_NDP_Simulator VERSION 0.1 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE RelWithDebInfo CACHE STRING "Build type" FORCE)
endif()
option(NDPSIM_SANITIZE "Instrument backend lifecycle probes with AddressSanitizer" OFF)

include(CTest)
if(BUILD_TESTING)
    set(NDPSIM_SDK_ROOT "${PROJECT_SOURCE_DIR}/sdk")
    # Native reference is built independently of the simulation targets.
    set(NDPSIM_GRAPH_CAPTURE_TESTING ON)
    set(NDPSIM_GRAPH_PYTHON_ROOT "${PROJECT_SOURCE_DIR}/python")
    set(NDPSIM_GRAPH_OPERATORS "${PROJECT_SOURCE_DIR}/extensions/graph/operators.json")
    add_subdirectory(workloads/graph-search)
    set_target_properties(ndpsim_native PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${PROJECT_BINARY_DIR}")
else()
    add_subdirectory(sdk)
endif()

file(READ "${PROJECT_SOURCE_DIR}/schemas/ndpsim-cost-profile-v1.schema.json" COST_PROFILE_SCHEMA)
file(READ "${PROJECT_SOURCE_DIR}/schemas/ndpsim-contract-v2.schema.json" COST_CONTRACT_SCHEMA)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    schemas/ndpsim-cost-profile-v1.schema.json schemas/ndpsim-contract-v2.schema.json)
configure_file(src/model/cost/schemas.h.in generated/model/cost/schemas.h @ONLY)
add_subdirectory(src/model/cost)


set(SST_ROOT "" CACHE PATH "Existing SST 16.0.0 prefix; empty uses the shared SST cache")
if(SST_ROOT)
    get_filename_component(NDPSIM_SST_PREFIX "${SST_ROOT}" ABSOLUTE BASE_DIR "${PROJECT_SOURCE_DIR}")
    set(NDPSIM_SST_MODE external)
else()
    file(READ "${PROJECT_SOURCE_DIR}/deps.lock.json" _lock)
    string(JSON _locked_python GET "${_lock}" tools python path)
    if(NOT DEFINED Python3_EXECUTABLE AND EXISTS "${_locked_python}")
        set(Python3_EXECUTABLE "${_locked_python}" CACHE FILEPATH "Python used by SST")
    endif()
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
    set(NDPSIM_SST_BUILD_JOBS 2 CACHE STRING "Parallel make jobs for SST")
    set(NDPSIM_PYTHON_CONFIG "" CACHE FILEPATH "Optional matching python-config")
    set(NDPSIM_SST_CACHE_DIR "${PROJECT_SOURCE_DIR}/.cache/sst" CACHE PATH
        "Shared SST source, build and installation cache")
    get_filename_component(_sst_cache "${NDPSIM_SST_CACHE_DIR}" ABSOLUTE BASE_DIR "${PROJECT_SOURCE_DIR}")
    set(NDPSIM_SST_MODE bundled)
    string(TOUPPER "${CMAKE_BUILD_TYPE}" _build_type)
    execute_process(COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tools/prepare_sst.py"
        "--cache-dir=${_sst_cache}" "--prefix-file=${PROJECT_BINARY_DIR}/sst-prefix.txt"
        "--cc=${CMAKE_C_COMPILER}" "--cxx=${CMAKE_CXX_COMPILER}"
        "--cflags=${CMAKE_C_FLAGS} ${CMAKE_C_FLAGS_${_build_type}}"
        "--cxxflags=${CMAKE_CXX_FLAGS} ${CMAKE_CXX_FLAGS_${_build_type}}"
        "--ldflags=${CMAKE_EXE_LINKER_FLAGS}" "--sdk=${CMAKE_OSX_SYSROOT}"
        "--arch=${CMAKE_OSX_ARCHITECTURES}" "--deployment-target=${CMAKE_OSX_DEPLOYMENT_TARGET}"
        "--python-config=${NDPSIM_PYTHON_CONFIG}" "--jobs=${NDPSIM_SST_BUILD_JOBS}"
        COMMAND_ERROR_IS_FATAL ANY)
    file(READ "${PROJECT_BINARY_DIR}/sst-prefix.txt" NDPSIM_SST_PREFIX)
    string(STRIP "${NDPSIM_SST_PREFIX}" NDPSIM_SST_PREFIX)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
        tools/prepare_sst.py deps.lock.json)
endif()
set(SST_DIR "${NDPSIM_SST_PREFIX}/lib/cmake/SST")
find_package(SST 16.0.0 EXACT REQUIRED CONFIG PATHS "${SST_DIR}" NO_DEFAULT_PATH)
set(NDPSIM_SST_EXECUTABLE "${NDPSIM_SST_PREFIX}/bin/sst")
set(NDPSIM_SST_INFO_EXECUTABLE "${NDPSIM_SST_PREFIX}/bin/sst-info")
file(WRITE "${PROJECT_BINARY_DIR}/sst-paths.json"
    "{\n  \"mode\": \"${NDPSIM_SST_MODE}\",\n  \"prefix\": \"${NDPSIM_SST_PREFIX}\",\n  \"sst\": \"${NDPSIM_SST_EXECUTABLE}\",\n  \"sst_info\": \"${NDPSIM_SST_INFO_EXECUTABLE}\"\n}\n")

# Develop and compile the MQSim fork in place. The native oracle is built
# separately from the unmodified commit recorded in deps.lock.json.
set(MQSIM_SOURCE "${CMAKE_SOURCE_DIR}/third_party/MQSim")
file(GLOB_RECURSE MQSIM_SOURCES CONFIGURE_DEPENDS "${MQSIM_SOURCE}/src/*.cpp")
list(REMOVE_ITEM MQSIM_SOURCES "${MQSIM_SOURCE}/src/main.cpp")
add_library(mqsim_pic STATIC ${MQSIM_SOURCES})
set_target_properties(mqsim_pic PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(mqsim_pic PUBLIC "${MQSIM_SOURCE}/src")
target_compile_options(mqsim_pic PRIVATE -ffp-contract=off)

find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
add_library(ndpsim_backend STATIC src/backend/lifecycle.cc src/backend/device_config.cc)
set_target_properties(ndpsim_backend PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(ndpsim_backend PUBLIC src PRIVATE "${PROJECT_SOURCE_DIR}/third_party/sst-core/external")
target_link_libraries(ndpsim_backend PUBLIC mqsim_pic ndpsim::capture)

if(NDPSIM_SANITIZE)
    foreach(target mqsim_pic ndpsim_backend)
        target_compile_options(${target} PUBLIC -fsanitize=address -fno-omit-frame-pointer)
        target_link_options(${target} PUBLIC -fsanitize=address)
    endforeach()
endif()

if(BUILD_TESTING)
    # Ordinary NVMe and bridge probes are fixtures, outside the product build.
    add_library(ndpsim_device_fixture INTERFACE)
    target_include_directories(ndpsim_device_fixture INTERFACE tests)
    target_compile_definitions(ndpsim_device_fixture INTERFACE
        NDPSIM_TEST_DEVICE_DIR="${PROJECT_SOURCE_DIR}/configs/devices")
    add_library(ndpsim MODULE src/sst/system.cc src/sst/bridge_poc.cc src/sst/event_bridge.cc src/sst/ordinary_io.cc)
    target_include_directories(ndpsim PRIVATE tests "${CMAKE_BINARY_DIR}/generated")
    target_link_libraries(ndpsim PRIVATE ndpsim_device_fixture ndpsim_backend SST::SSTCore)
    if(APPLE)
        # SST resolves its core symbols when loading the element, as in its helper.
        target_link_options(ndpsim PRIVATE "-undefined" "dynamic_lookup")
    endif()
    target_compile_options(ndpsim PRIVATE -Wall -Wextra)

    add_test(NAME benchmark_artifact_schema COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tests/test_benchmark_artifacts.py")
    add_test(NAME benchmark_data_schema COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tests/test_benchmark_data.py")
    add_test(NAME m0_evidence_tools COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tests/test_m0.py")
    add_executable(lifecycle_probe tests/lifecycle_probe.cc)
    target_link_libraries(lifecycle_probe PRIVATE ndpsim_device_fixture ndpsim_backend)
    add_test(NAME backend_lifecycle COMMAND lifecycle_probe)
    add_test(NAME backend_error COMMAND lifecycle_probe error)
    add_test(NAME backend_early_teardown COMMAND lifecycle_probe early)
    add_test(NAME backend_invalid_mqsim_config COMMAND lifecycle_probe invalid-mqsim)
    add_executable(local_read_device_probe tests/local_read_device_probe.cc)
    target_include_directories(local_read_device_probe PRIVATE "${CMAKE_BINARY_DIR}/generated")
    target_link_libraries(local_read_device_probe PRIVATE ndpsim_device_fixture ndpsim_backend)
    add_test(NAME backend_local_read_device COMMAND local_read_device_probe)
    add_executable(local_read_probe tests/local_read_probe.cc)
    target_link_libraries(local_read_probe PRIVATE ndpsim_backend)
    add_test(NAME backend_local_read_reverse COMMAND local_read_probe)
    add_test(NAME backend_local_read_forward COMMAND local_read_probe forward)
    add_test(NAME backend_local_read_unsupported COMMAND local_read_probe unsupported)
    # Ordinary-route regression executable; never used as the native oracle.
    add_executable(mqsim_development_cli "${MQSIM_SOURCE}/src/main.cpp")
    target_link_libraries(mqsim_development_cli PRIVATE mqsim_pic)
    add_executable(event_bridge_probe tests/event_bridge_probe.cc)
    target_link_libraries(event_bridge_probe PRIVATE ndpsim_backend)
    add_test(NAME backend_event_bridge COMMAND event_bridge_probe)
    add_test(NAME backend_native_event_loop COMMAND event_bridge_probe native)
    if(NOT NDPSIM_SANITIZE)
        add_test(NAME sst_event_bridge COMMAND "${NDPSIM_SST_EXECUTABLE}"
            "--add-lib-path=$<TARGET_FILE_DIR:ndpsim>" "${PROJECT_SOURCE_DIR}/configs/regression/bridge_poc.py")
        set_tests_properties(sst_event_bridge PROPERTIES PASS_REGULAR_EXPRESSION "E1 PASS events=8" TIMEOUT 30)
        add_test(NAME sst_element_load COMMAND "${NDPSIM_SST_INFO_EXECUTABLE}"
            "--add-lib-path=$<TARGET_FILE_DIR:ndpsim>" ndpsim.System)
        set_tests_properties(sst_element_load PROPERTIES PASS_REGULAR_EXPRESSION "Component 0: System" TIMEOUT 30)
        add_test(NAME sst_lifecycle COMMAND "${NDPSIM_SST_EXECUTABLE}"
            "--add-lib-path=$<TARGET_FILE_DIR:ndpsim>" "${PROJECT_SOURCE_DIR}/configs/regression/lifecycle.py")
        set_tests_properties(sst_lifecycle PROPERTIES
            PASS_REGULAR_EXPRESSION "finish SST=1 ns MQSim=0 ns; device destroyed" TIMEOUT 30)
    endif()
endif()

if(BUILD_TESTING)
    add_executable(formal_bridge_probe tests/formal_bridge_probe.cc)
    target_link_libraries(formal_bridge_probe PRIVATE ndpsim_backend)
    add_test(NAME formal_bridge_guards COMMAND formal_bridge_probe)
    if(NOT NDPSIM_SANITIZE)
        add_test(NAME sst_formal_bridge COMMAND "${NDPSIM_SST_EXECUTABLE}" "--add-lib-path=$<TARGET_FILE_DIR:ndpsim>" "${PROJECT_SOURCE_DIR}/configs/regression/event_bridge.py")
        set_tests_properties(sst_formal_bridge PROPERTIES PASS_REGULAR_EXPRESSION "M1.2 PASS" TIMEOUT 30)
    endif()
endif()

if(BUILD_TESTING)
    add_executable(ordinary_io_probe tests/ordinary_io_probe.cc)
    target_include_directories(ordinary_io_probe PRIVATE "${CMAKE_BINARY_DIR}/generated")
    target_link_libraries(ordinary_io_probe PRIVATE ndpsim_device_fixture ndpsim_backend)
    add_test(NAME ordinary_qd1 COMMAND ordinary_io_probe)
    add_test(NAME ordinary_cmt_miss_concurrent COMMAND ordinary_io_probe miss concurrent)
endif()

if(BUILD_TESTING)
    add_executable(observation_probe tests/observation_probe.cc)
    target_link_libraries(observation_probe PRIVATE ndpsim_backend)
    add_test(NAME observation_intervals COMMAND observation_probe)
endif()

if(BUILD_TESTING)
    add_executable(profile_probe tests/profile_probe.cc)
    target_link_libraries(profile_probe PRIVATE ndpsim_device_fixture ndpsim_backend)
    foreach(profile native-small ann-2g-v1 ann-16g-v1)
        add_test(NAME profile_${profile} COMMAND profile_probe ${profile})
        add_test(NAME profile_${profile}_namespace_overflow COMMAND profile_probe ${profile} overflow)
    endforeach()
endif()

if(BUILD_TESTING)
    add_executable(cost_model_probe tests/cost_model_probe.cc)
    target_include_directories(cost_model_probe PRIVATE "${PROJECT_SOURCE_DIR}/third_party/sst-core/external")
    target_link_libraries(cost_model_probe PRIVATE ndpsim::cost)
    add_test(NAME cost_model_v045 COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_v045_cost_model.py" "$<TARGET_FILE:cost_model_probe>")
endif()

if(BUILD_TESTING)
    add_executable(fit_profile_probe tests/fit_profile_probe.cc)
    target_include_directories(fit_profile_probe PRIVATE "${PROJECT_SOURCE_DIR}/third_party/sst-core/external")
    target_link_libraries(fit_profile_probe PRIVATE ndpsim::cost)
    add_test(NAME independent_fit_v045 COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_v045_fitting.py" "$<TARGET_FILE:fit_profile_probe>")
endif()

# P07: metadata-only service/token foundation. No content, functional operators,
# native graph or legacy runtime dependency. SST remains the time authority.
add_library(ndpsim_timing STATIC src/timing/runtime.cc src/timing/resources.cc)
set_target_properties(ndpsim_timing PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(ndpsim_timing PUBLIC src sdk/include)
target_link_libraries(ndpsim_timing PUBLIC mqsim_pic)
target_compile_options(ndpsim_timing PRIVATE -Wall -Wextra -Wpedantic)
if(BUILD_TESTING)
    add_executable(timing_runtime_probe tests/timing_runtime_probe.cc)
    target_link_libraries(timing_runtime_probe PRIVATE ndpsim_timing)
    add_test(NAME timing_runtime_v045 COMMAND timing_runtime_probe)
endif()
if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    add_library(ndpsim_timing_validation MODULE src/sst/timing_validation.cc)
    target_link_libraries(ndpsim_timing_validation PRIVATE ndpsim_timing SST::SSTCore)
    if(APPLE)
        target_link_options(ndpsim_timing_validation PRIVATE "-undefined" "dynamic_lookup")
    endif()
    foreach(variant base input service return zero)
        add_test(NAME sst_timing_runtime_${variant} COMMAND "${NDPSIM_SST_EXECUTABLE}"
            "--add-lib-path=$<TARGET_FILE_DIR:ndpsim_timing_validation>"
            "${PROJECT_SOURCE_DIR}/configs/regression/timing_runtime.py")
        set_tests_properties(sst_timing_runtime_${variant} PROPERTIES
            PASS_REGULAR_EXPRESSION "P07 SST PASS" TIMEOUT 30)
        if(variant STREQUAL "zero")
            set_tests_properties(sst_timing_runtime_${variant} PROPERTIES ENVIRONMENT
                "NDPSIM_TIMING_INPUT=0;NDPSIM_TIMING_SERVICE=0;NDPSIM_TIMING_RETURN=0")
        elseif(NOT variant STREQUAL "base")
            string(TOUPPER "${variant}" _timing_delay)
            set_tests_properties(sst_timing_runtime_${variant} PROPERTIES
                ENVIRONMENT "NDPSIM_TIMING_${_timing_delay}=107")
        endif()
    endforeach()
endif()

# P08: full query dependency indexes on bounded SQLite scratch; stage details are
# read by original IR offsets. No functional graph or legacy Replay dependency.
find_package(SQLite3 REQUIRED)
add_library(ndpsim_dag STATIC src/dag/package.cc src/dag/executor.cc)
set_target_properties(ndpsim_dag PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(ndpsim_dag PUBLIC src sdk/include
    PRIVATE "${PROJECT_SOURCE_DIR}/third_party/sst-core/external")
target_link_libraries(ndpsim_dag PUBLIC ndpsim_timing ndpsim::capture SQLite3::SQLite3)
target_compile_options(ndpsim_dag PRIVATE -Wall -Wextra -Wpedantic)
if(BUILD_TESTING)
    add_executable(dag_executor_probe tests/dag_executor_probe.cc)
    target_include_directories(dag_executor_probe PRIVATE "${PROJECT_SOURCE_DIR}/third_party/sst-core/external")
    target_link_libraries(dag_executor_probe PRIVATE ndpsim_dag)
    add_test(NAME static_dag_v045 COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_v045_dag.py" "$<TARGET_FILE:dag_executor_probe>")
    set_tests_properties(static_dag_v045 PROPERTIES TIMEOUT 180)
endif()
add_library(ndpsim_dag_sst STATIC src/sst/dag_host.cc)
set_target_properties(ndpsim_dag_sst PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(ndpsim_dag_sst PUBLIC ndpsim_dag SST::SSTCore)
target_compile_options(ndpsim_dag_sst PRIVATE -Wall -Wextra -Wpedantic)
if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    add_library(ndpsim_dag_validation MODULE tests/dag_sst_probe.cc)
    target_include_directories(ndpsim_dag_validation PRIVATE "${PROJECT_SOURCE_DIR}/third_party/sst-core/external")
    target_link_libraries(ndpsim_dag_validation PRIVATE ndpsim_dag_sst)
    if(APPLE)
        target_link_options(ndpsim_dag_validation PRIVATE "-undefined" "dynamic_lookup")
    endif()
    add_test(NAME sst_static_dag_v045 COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_v045_dag_sst.py" "${PROJECT_BINARY_DIR}")
    set_tests_properties(sst_static_dag_v045 PROPERTIES TIMEOUT 60)
endif()

# P09 metadata-only physical layout, independent of content and device services.
add_library(ndpsim_layout STATIC src/layout/layout.cc)
set_target_properties(ndpsim_layout PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(ndpsim_layout PUBLIC src sdk/include
    PRIVATE "${PROJECT_SOURCE_DIR}/third_party/sst-core/external")
target_link_libraries(ndpsim_layout PUBLIC ndpsim::capture)
target_compile_options(ndpsim_layout PRIVATE -Wall -Wextra -Wpedantic)
if(NDPSIM_SANITIZE)
    target_compile_options(ndpsim_layout PUBLIC -fsanitize=address -fno-omit-frame-pointer)
    target_link_options(ndpsim_layout PUBLIC -fsanitize=address)
endif()
if(BUILD_TESTING)
    add_executable(layout_metadata_probe tests/layout_metadata_probe.cc)
    target_include_directories(layout_metadata_probe PRIVATE "${PROJECT_SOURCE_DIR}/third_party/sst-core/external")
    target_link_libraries(layout_metadata_probe PRIVATE ndpsim_layout)
    add_test(NAME layout_metadata_v045 COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_v045_layout.py" "$<TARGET_FILE:layout_metadata_probe>")
endif()

if(BUILD_TESTING)
    add_executable(device_config_probe tests/device_config_probe.cc)
    target_include_directories(device_config_probe PRIVATE "${PROJECT_SOURCE_DIR}/third_party/sst-core/external" "${PROJECT_BINARY_DIR}/generated")
    target_link_libraries(device_config_probe PRIVATE ndpsim_backend ndpsim_layout)
    add_test(NAME device_config_v045 COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_v045_device_config.py" "$<TARGET_FILE:device_config_probe>")
    set_tests_properties(device_config_v045 PROPERTIES TIMEOUT 120)
endif()

# P11: resident lowering consumes metadata/Profile only; no device is constructed.
add_library(ndpsim_memory_plan STATIC src/mapping/memory.cc)
set_target_properties(ndpsim_memory_plan PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(ndpsim_memory_plan PUBLIC "${PROJECT_SOURCE_DIR}/third_party/sst-core/external")
target_link_libraries(ndpsim_memory_plan PUBLIC ndpsim_dag ndpsim::cost)
target_compile_options(ndpsim_memory_plan PRIVATE -Wall -Wextra -Wpedantic)
if(NOT NDPSIM_SANITIZE)
    add_library(ndpsim_memory MODULE src/sst/memory_host.cc)
    target_link_libraries(ndpsim_memory PRIVATE ndpsim_memory_plan ndpsim_dag_sst)
    if(APPLE)
        target_link_options(ndpsim_memory PRIVATE "-undefined" "dynamic_lookup")
    endif()
    if(BUILD_TESTING)
        add_test(NAME sst_memory_timing_v045 COMMAND "${Python3_EXECUTABLE}"
            "${PROJECT_SOURCE_DIR}/tests/test_v045_memory.py" "${PROJECT_BINARY_DIR}")
        set_tests_properties(sst_memory_timing_v045 PROPERTIES TIMEOUT 120)
        add_test(NAME unified_cli_v045 COMMAND "${Python3_EXECUTABLE}"
            "${PROJECT_SOURCE_DIR}/tests/test_v045_cli.py" "${PROJECT_BINARY_DIR}")
        set_tests_properties(unified_cli_v045 PROPERTIES TIMEOUT 180)
        add_test(NAME layered_validator_v045 COMMAND "${Python3_EXECUTABLE}"
            "${PROJECT_SOURCE_DIR}/tests/test_v045_validation.py" "${PROJECT_BINARY_DIR}")
        set_tests_properties(layered_validator_v045 PROPERTIES TIMEOUT 240)
    endif()
endif()

if(BUILD_TESTING)
    add_executable(memory_plan_probe tests/memory_plan_probe.cc)
    target_link_libraries(memory_plan_probe PRIVATE ndpsim_memory_plan)
    add_test(NAME memory_plan_v045 COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_v045_memory_plan.py" "${PROJECT_BINARY_DIR}")
endif()

# P12 metadata-only wire descriptors, local reads and Controller service endpoint.
add_library(ndpsim_controller_timing STATIC src/timing/messages.cc src/timing/controller.cc)
set_target_properties(ndpsim_controller_timing PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(ndpsim_controller_timing PUBLIC ndpsim_timing ndpsim_layout ndpsim::cost)
target_compile_options(ndpsim_controller_timing PRIVATE -Wall -Wextra -Wpedantic)
if(BUILD_TESTING)
    add_executable(controller_timing_probe tests/controller_timing_probe.cc)
    target_include_directories(controller_timing_probe PRIVATE "${PROJECT_BINARY_DIR}/generated")
    target_link_libraries(controller_timing_probe PRIVATE ndpsim_device_fixture ndpsim_controller_timing ndpsim_backend)
    add_test(NAME controller_timing_v045 COMMAND controller_timing_probe)
    foreach(mode reverse request transaction unknown duplicate)
        add_test(NAME metadata_local_read_${mode} COMMAND controller_timing_probe local "${mode}")
    endforeach()
endif()
if(BUILD_TESTING)
    foreach(mode reserved command local-read staged compute-ready computed result input-flight compute-flight return-flight delay-input delay-compute delay-return zero-compute credit-wait credit-failure)
        add_test(NAME controller_timing_${mode} COMMAND controller_timing_probe "${mode}")
    endforeach()
endif()
if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    add_library(ndpsim_controller_validation MODULE tests/controller_timing_sst.cc)
    target_include_directories(ndpsim_controller_validation PRIVATE tests "${PROJECT_BINARY_DIR}/generated")
    target_link_libraries(ndpsim_controller_validation PRIVATE ndpsim_device_fixture ndpsim_controller_timing ndpsim_backend SST::SSTCore)
    if(APPLE)
        target_link_options(ndpsim_controller_validation PRIVATE "-undefined" "dynamic_lookup")
    endif()
    foreach(mode base input-flight compute-flight return-flight delay-input delay-compute delay-return zero-compute credit-wait credit-failure)
        add_test(NAME sst_controller_timing_${mode} COMMAND "${NDPSIM_SST_EXECUTABLE}"
            "--add-lib-path=$<TARGET_FILE_DIR:ndpsim_controller_validation>"
            "${PROJECT_SOURCE_DIR}/configs/regression/controller_timing.py")
        set_tests_properties(sst_controller_timing_${mode} PROPERTIES PASS_REGULAR_EXPRESSION "P12 Controller PASS" TIMEOUT 30)
        if(NOT mode STREQUAL "base")
            set_tests_properties(sst_controller_timing_${mode} PROPERTIES ENVIRONMENT "NDPSIM_CONTROLLER_MODE=${mode}")
        endif()
    endforeach()
endif()

# P13 graph extension: size-only SSD Host and Controller mappings.
add_library(ndpsim_graph_ssd_plan STATIC src/extensions/graph/ssd.cc)
set_target_properties(ndpsim_graph_ssd_plan PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(ndpsim_graph_ssd_plan PUBLIC "${PROJECT_BINARY_DIR}/generated")
target_link_libraries(ndpsim_graph_ssd_plan PUBLIC ndpsim_memory_plan ndpsim_controller_timing ndpsim_backend)
target_compile_options(ndpsim_graph_ssd_plan PRIVATE -Wall -Wextra -Wpedantic)
if(NOT NDPSIM_SANITIZE)
    add_library(ndpsim_graph_ssd MODULE src/sst/ssd_host.cc)
    target_link_libraries(ndpsim_graph_ssd PRIVATE ndpsim_graph_ssd_plan SST::SSTCore)
    if(APPLE)
        target_link_options(ndpsim_graph_ssd PRIVATE "-undefined" "dynamic_lookup")
    endif()
endif()
if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    add_test(NAME graph_ssd_v045 COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_v045_graph_ssd.py" "${PROJECT_BINARY_DIR}")
    set_tests_properties(graph_ssd_v045 PROPERTIES TIMEOUT 180)
endif()
if(BUILD_TESTING)
    add_executable(graph_ssd_probe tests/graph_ssd_probe.cc)
    target_link_libraries(graph_ssd_probe PRIVATE ndpsim_graph_ssd_plan)
    if(NDPSIM_SANITIZE)
        add_test(NAME graph_ssd_v045 COMMAND "${CMAKE_COMMAND}" -E env NDPSIM_SSD_NATIVE=1
            "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tests/test_v045_graph_ssd.py" "${PROJECT_BINARY_DIR}")
        set_tests_properties(graph_ssd_v045 PROPERTIES TIMEOUT 180)
    endif()
endif()

if(BUILD_TESTING)
    add_test(NAME mqsim_oracle_v045 COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_v045_mqsim.py" "${PROJECT_BINARY_DIR}")
    set_tests_properties(mqsim_oracle_v045 PROPERTIES TIMEOUT 120)
endif()

# P17: compile-time teaching extension registration; implementation stays external.
add_subdirectory(examples/extension)
