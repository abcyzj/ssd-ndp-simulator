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
option(NDPSIM_NATIVE_ONLY "Build only the standalone graph search" OFF)
option(NDPSIM_SANITIZE "Instrument backend lifecycle probes with AddressSanitizer" OFF)

# Immutable logical content and target residency; no MQSim/SST ownership or timing.
add_library(ndpsim_content STATIC src/content/content.cc src/content/sha256.cc workloads/graph-search/src/content/value.cc)
set_target_properties(ndpsim_content PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(ndpsim_content PUBLIC src workloads/graph-search/src)
target_compile_options(ndpsim_content PRIVATE -Wall -Wextra -Wpedantic)
if(NDPSIM_SANITIZE)
    target_compile_options(ndpsim_content PUBLIC -fsanitize=address -fno-omit-frame-pointer)
    target_link_options(ndpsim_content PUBLIC -fsanitize=address)
endif()
add_library(ndpsim_operators STATIC workloads/graph-search/src/operators/operators.cc)
set_target_properties(ndpsim_operators PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(ndpsim_operators PUBLIC ndpsim_content)
# Fixed dimension-order binary64 operations, including under Release/global fast-math flags.
target_compile_options(ndpsim_operators PRIVATE -Wall -Wextra -fno-fast-math -ffp-contract=off)
option(NDPSIM_SLOW_FUNCTIONAL "Acceptance-only equivalent L2 implementation with repeated arithmetic" OFF)
if(NDPSIM_SLOW_FUNCTIONAL)
    target_compile_definitions(ndpsim_operators PRIVATE NDPSIM_SLOW_FUNCTIONAL=1)
endif()
# Shared pure graph operations; no Runtime, HostCpu, SST or MQSim link dependency.
add_library(ndpsim_algorithm STATIC workloads/graph-search/src/search/core.cc)
set_target_properties(ndpsim_algorithm PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(ndpsim_algorithm PUBLIC ndpsim_operators)
target_compile_options(ndpsim_algorithm PRIVATE -Wall -Wextra -Wpedantic -fno-fast-math -ffp-contract=off)
find_package(Threads REQUIRED)
add_subdirectory(sdk)
add_executable(ndpsim_native workloads/graph-search/src/search/native.cc)
target_link_libraries(ndpsim_native PRIVATE ndpsim_algorithm Threads::Threads ndpsim::capture)
target_compile_options(ndpsim_native PRIVATE -Wall -Wextra -Wpedantic -fno-fast-math -ffp-contract=off)
# Embed source identity from the actual native build inputs.
set(_native_identity "")
foreach(_source CMakeLists.txt workloads/graph-search/src/search/native.cc workloads/graph-search/src/search/native_observation.h
        workloads/graph-search/src/search/core.cc workloads/graph-search/src/search/core.h workloads/graph-search/src/operators/operators.cc workloads/graph-search/src/operators/operators.h
        src/content/content.cc src/content/content.h src/content/sha256.cc
        workloads/graph-search/src/content/value.h workloads/graph-search/src/content/value.cc
        workloads/graph-search/src/search/capture_v2.h workloads/graph-search/src/search/graph_specs.h
        sdk/src/capture.cc sdk/src/sha256.cc sdk/include/ndpsim/capture.h sdk/CMakeLists.txt)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${_source}")
    file(SHA256 "${PROJECT_SOURCE_DIR}/${_source}" _sha)
    string(APPEND _native_identity "${_source}=${_sha}\n")
endforeach()
string(SHA256 _native_sha "${_native_identity}")
set(_native_flags "${CMAKE_CXX_FLAGS} -std=c++17 -Wall -Wextra -Wpedantic -fno-fast-math -ffp-contract=off")
foreach(_config Debug Release MinSizeRel RelWithDebInfo)
    string(TOUPPER "${_config}" _upper)
    string(APPEND _native_flags " $<$<CONFIG:${_config}>:${CMAKE_CXX_FLAGS_${_upper}}>")
endforeach()
if(NDPSIM_SLOW_FUNCTIONAL)
    string(APPEND _native_flags " -DNDPSIM_SLOW_FUNCTIONAL=1")
endif()
if(NDPSIM_SANITIZE)
    string(APPEND _native_flags " -fsanitize=address -fno-omit-frame-pointer")
endif()
target_compile_definitions(ndpsim_native PRIVATE
    NDPSIM_NATIVE_SOURCE_SHA256="${_native_sha}" NDPSIM_NATIVE_FLAGS="${_native_flags}")
if(NDPSIM_NATIVE_ONLY)
    return()
endif()

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
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS configs/native/ssdconfig.xml)
file(GLOB_RECURSE MQSIM_SOURCES CONFIGURE_DEPENDS "${MQSIM_SOURCE}/src/*.cpp")
list(REMOVE_ITEM MQSIM_SOURCES "${MQSIM_SOURCE}/src/main.cpp")
add_library(mqsim_pic STATIC ${MQSIM_SOURCES})
set_target_properties(mqsim_pic PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(mqsim_pic PUBLIC "${MQSIM_SOURCE}/src")
target_compile_options(mqsim_pic PRIVATE -ffp-contract=off)

# One descriptor drives embedded XML, partition geometry, and run snapshots.
find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/generated")
execute_process(COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tools/device_profiles.py"
    --header "${CMAKE_BINARY_DIR}/generated/profile.h" COMMAND_ERROR_IS_FATAL ANY)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    tools/device_profiles.py configs/profiles/devices.json)
add_library(ndpsim_backend STATIC src/backend/lifecycle.cc)
set_target_properties(ndpsim_backend PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(ndpsim_backend PUBLIC src PRIVATE "${CMAKE_BINARY_DIR}/generated")
target_link_libraries(ndpsim_backend PUBLIC mqsim_pic)

add_library(ndpsim MODULE src/sst/system.cc src/sst/bridge_poc.cc src/sst/event_bridge.cc)
target_include_directories(ndpsim PRIVATE tests)
target_link_libraries(ndpsim PRIVATE ndpsim_backend SST::SSTCore)
if(APPLE)
    # SST resolves its core symbols when loading the element, as in its helper.
    target_link_options(ndpsim PRIVATE "-undefined" "dynamic_lookup")
endif()
target_compile_options(ndpsim PRIVATE -Wall -Wextra)

if(NDPSIM_SANITIZE)
    foreach(target mqsim_pic ndpsim_backend)
        target_compile_options(${target} PUBLIC -fsanitize=address -fno-omit-frame-pointer)
        target_link_options(${target} PUBLIC -fsanitize=address)
    endforeach()
endif()

include(CTest)
if(BUILD_TESTING)
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
    add_test(NAME device_profiles COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tests/test_device_profiles.py")
    add_test(NAME simulation_input_schema COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tests/test_simulation_input.py")
    add_test(NAME benchmark_artifact_schema COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tests/test_benchmark_artifacts.py")
    add_test(NAME benchmark_data_schema COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tests/test_benchmark_data.py")
    add_test(NAME m0_evidence_tools COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tests/test_m0.py")
    add_executable(lifecycle_probe tests/lifecycle_probe.cc)
    target_link_libraries(lifecycle_probe PRIVATE ndpsim_backend)
    add_test(NAME backend_lifecycle COMMAND lifecycle_probe)
    add_test(NAME backend_error COMMAND lifecycle_probe error)
    add_test(NAME backend_early_teardown COMMAND lifecycle_probe early)
    add_test(NAME backend_invalid_mqsim_config COMMAND lifecycle_probe invalid-mqsim)
    add_executable(local_read_device_probe tests/local_read_device_probe.cc)
    target_include_directories(local_read_device_probe PRIVATE "${CMAKE_BINARY_DIR}/generated")
    target_link_libraries(local_read_device_probe PRIVATE ndpsim_backend)
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

add_custom_target(run_lifecycle
    COMMAND "${NDPSIM_SST_EXECUTABLE}" "--add-lib-path=$<TARGET_FILE_DIR:ndpsim>"
        "${PROJECT_SOURCE_DIR}/configs/regression/lifecycle.py"
    DEPENDS ndpsim USES_TERMINAL)

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
    target_link_libraries(ordinary_io_probe PRIVATE ndpsim_backend)
    add_test(NAME ordinary_qd1 COMMAND ordinary_io_probe)
    add_test(NAME ordinary_cmt_miss_concurrent COMMAND ordinary_io_probe miss concurrent)
endif()
target_sources(ndpsim PRIVATE src/sst/ordinary_io.cc src/sst/m0_preload.cc)
target_include_directories(ndpsim PRIVATE "${CMAKE_BINARY_DIR}/generated")

if(BUILD_TESTING)
    add_executable(observation_probe tests/observation_probe.cc)
    target_link_libraries(observation_probe PRIVATE ndpsim_backend)
    add_test(NAME observation_intervals COMMAND observation_probe)
endif()

if(BUILD_TESTING)
    add_executable(mapped_content_probe tests/mapped_content_probe.cc)
    target_link_libraries(mapped_content_probe PRIVATE ndpsim_content)
    add_test(NAME mapped_content COMMAND mapped_content_probe)
    add_executable(profile_probe tests/profile_probe.cc)
    target_include_directories(profile_probe PRIVATE "${CMAKE_BINARY_DIR}/generated")
    target_link_libraries(profile_probe PRIVATE ndpsim_backend)
    foreach(profile native-small ann-2g-v1 ann-16g-v1)
        add_test(NAME profile_${profile} COMMAND profile_probe ${profile})
        add_test(NAME profile_${profile}_namespace_overflow COMMAND profile_probe ${profile} overflow)
    endforeach()
    add_executable(content_probe tests/content_probe.cc)
    target_link_libraries(content_probe PRIVATE ndpsim_content)
    add_test(NAME content_layout_handles COMMAND content_probe)
    add_executable(content_backend_probe tests/content_backend_probe.cc)
    target_include_directories(content_backend_probe PRIVATE "${CMAKE_BINARY_DIR}/generated")
    target_link_libraries(content_backend_probe PRIVATE ndpsim_content ndpsim_backend)
    add_test(NAME content_backend_association COMMAND content_backend_probe)
endif()

# Result gating and continuations use the existing SST-owned bridge, no clock of their own.
add_library(ndpsim_runtime STATIC src/runtime/runtime.cc)
set_target_properties(ndpsim_runtime PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(ndpsim_runtime PUBLIC ndpsim_content ndpsim_backend)
target_compile_options(ndpsim_runtime PRIVATE -Wall -Wextra -Wpedantic)
if(BUILD_TESTING)
    add_executable(runtime_probe tests/runtime_probe.cc)
    target_link_libraries(runtime_probe PRIVATE ndpsim_runtime)
    add_test(NAME runtime_dependencies_visibility COMMAND runtime_probe)
endif()
target_sources(ndpsim PRIVATE src/sst/runtime_validation.cc)
target_link_libraries(ndpsim PRIVATE ndpsim_runtime)
if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    add_test(NAME sst_runtime_visibility COMMAND "${NDPSIM_SST_EXECUTABLE}"
        "--add-lib-path=$<TARGET_FILE_DIR:ndpsim>" "${PROJECT_SOURCE_DIR}/configs/regression/runtime.py")
    set_tests_properties(sst_runtime_visibility PROPERTIES PASS_REGULAR_EXPRESSION "M2.2 SST PASS" TIMEOUT 30)
endif()

if(BUILD_TESTING)
    add_test(NAME reference_path_comparator COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_reference_paths.py")
    add_test(NAME native_search_batch COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_native_search.py" "$<TARGET_FILE:ndpsim_native>")
    add_test(NAME graph_capture_v2 COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/workloads/graph-search/tests/test_capture_v2.py" "$<TARGET_FILE:ndpsim_native>"
        "${PROJECT_SOURCE_DIR}/python" "${PROJECT_SOURCE_DIR}/extensions/graph/operators.json")
    add_test(NAME native_observation COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_native_observation.py" "$<TARGET_FILE:ndpsim_native>")
    add_executable(search_core_probe workloads/graph-search/tests/search_core_probe.cc)
    target_link_libraries(search_core_probe PRIVATE ndpsim_algorithm)
    add_test(NAME search_core_oracle COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_search_core.py" --probe "$<TARGET_FILE:search_core_probe>")
endif()

add_executable(bench_host_cpu EXCLUDE_FROM_ALL tools/bench_host_cpu.cc)
target_link_libraries(bench_host_cpu PRIVATE ndpsim_operators)
file(READ "${PROJECT_SOURCE_DIR}/schemas/ndpsim-cpu-profile-v1.schema.json" CPU_PROFILE_SCHEMA)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS schemas/ndpsim-cpu-profile-v1.schema.json)
configure_file(src/model/cpu_profile_schema.h.in generated/model/cpu_profile_schema.h @ONLY)
add_library(ndpsim_host STATIC src/model/host_cpu.cc src/model/cpu_profile.cc)
target_include_directories(ndpsim_host PRIVATE "${PROJECT_SOURCE_DIR}/third_party/sst-core/external"
    "${PROJECT_BINARY_DIR}/generated")
set_target_properties(ndpsim_host PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(ndpsim_host PUBLIC ndpsim_runtime ndpsim_operators)
target_compile_options(ndpsim_host PRIVATE -Wall -Wextra -fno-fast-math -ffp-contract=off)
if(BUILD_TESTING)
    add_executable(operator_probe tests/operator_probe.cc)
    target_link_libraries(operator_probe PRIVATE ndpsim_host)
    add_test(NAME operators_host_cost COMMAND operator_probe)
    add_executable(cpu_profile_probe tests/cpu_profile_probe.cc)
    target_include_directories(cpu_profile_probe PRIVATE "${PROJECT_SOURCE_DIR}/third_party/sst-core/external")
    target_link_libraries(cpu_profile_probe PRIVATE ndpsim_host)
    add_test(NAME cpu_profile_fail_closed COMMAND cpu_profile_probe
        "${PROJECT_SOURCE_DIR}/examples/v04-contract/cpu-profile.json"
        "${PROJECT_BINARY_DIR}/cpu-profile-probe.json")
endif()
target_sources(ndpsim PRIVATE src/sst/host_validation.cc)
target_link_libraries(ndpsim PRIVATE ndpsim_host)
if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    add_test(NAME sst_host_cost COMMAND "${NDPSIM_SST_EXECUTABLE}"
        "--add-lib-path=$<TARGET_FILE_DIR:ndpsim>" "${PROJECT_SOURCE_DIR}/configs/regression/host_cost.py")
    set_tests_properties(sst_host_cost PROPERTIES PASS_REGULAR_EXPRESSION "M2.3 SST PASS" TIMEOUT 30)
endif()

add_library(ndpsim_search STATIC src/search/graph.cc)
set_target_properties(ndpsim_search PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(ndpsim_search PUBLIC ndpsim_host)
target_compile_options(ndpsim_search PRIVATE -Wall -Wextra -fno-fast-math -ffp-contract=off)
target_sources(ndpsim PRIVATE src/sst/graph_host.cc src/search/replay.cc)
target_link_libraries(ndpsim PRIVATE ndpsim_search)
if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
    add_test(NAME sst_host_graph COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tools/run_graph.py"
        --config "${PROJECT_SOURCE_DIR}/configs/regression/host-branch.json" --build "${PROJECT_BINARY_DIR}"
        --output "${PROJECT_BINARY_DIR}/m3.1-ctest")
    set_tests_properties(sst_host_graph PROPERTIES PASS_REGULAR_EXPRESSION "Recall@2=" TIMEOUT 120)
endif()
if(BUILD_TESTING)
    add_executable(graph_probe tests/graph_probe.cc)
    target_link_libraries(graph_probe PRIVATE ndpsim_search)
    add_test(NAME graph_search_semantics COMMAND graph_probe)
endif()

# Formal local storage service; staging/credit and PCIe messages are separate tasks.
target_sources(ndpsim PRIVATE src/sst/local_read_validation.cc)
if(BUILD_TESTING)
    add_executable(formal_local_read_probe tests/formal_local_read_probe.cc)
    target_include_directories(formal_local_read_probe PRIVATE "${CMAKE_BINARY_DIR}/generated")
    target_link_libraries(formal_local_read_probe PRIVATE ndpsim_runtime)
    add_test(NAME formal_local_read COMMAND formal_local_read_probe)
    add_test(NAME formal_local_read_reverse COMMAND formal_local_read_probe reverse)
    foreach(fault fault transaction unknown duplicate)
        add_test(NAME formal_local_read_${fault} COMMAND formal_local_read_probe ${fault})
        set_tests_properties(formal_local_read_${fault} PROPERTIES WILL_FAIL TRUE)
    endforeach()
    if(NOT NDPSIM_SANITIZE)
        add_test(NAME sst_local_read COMMAND "${NDPSIM_SST_EXECUTABLE}" "--add-lib-path=$<TARGET_FILE_DIR:ndpsim>"
            "${PROJECT_SOURCE_DIR}/configs/regression/local_read.py")
        set_tests_properties(sst_local_read PROPERTIES PASS_REGULAR_EXPRESSION "M3.2a PASS" TIMEOUT 60)
    endif()
endif()

# MQSim owns the only host/device transfer queues and serialization cost.
add_library(ndpsim_transport STATIC src/transport/messages.cc)
set_target_properties(ndpsim_transport PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(ndpsim_transport PUBLIC ndpsim_runtime)
target_compile_options(ndpsim_transport PRIVATE -Wall -Wextra -fno-fast-math -ffp-contract=off)
target_sources(ndpsim PRIVATE src/sst/pcie_validation.cc)
target_link_libraries(ndpsim PRIVATE ndpsim_transport)
if(BUILD_TESTING)
    add_executable(pcie_probe tests/pcie_probe.cc)
    target_include_directories(pcie_probe PRIVATE tests "${CMAKE_BINARY_DIR}/generated")
    target_link_libraries(pcie_probe PRIVATE ndpsim_transport)
    add_test(NAME shared_pcie_mixed COMMAND pcie_probe)
    if(NOT NDPSIM_SANITIZE)
        add_test(NAME sst_shared_pcie COMMAND "${NDPSIM_SST_EXECUTABLE}" "--add-lib-path=$<TARGET_FILE_DIR:ndpsim>"
            "${PROJECT_SOURCE_DIR}/configs/regression/pcie.py")
        set_tests_properties(sst_shared_pcie PROPERTIES PASS_REGULAR_EXPRESSION "M3.2b PASS" TIMEOUT 60)
    endif()
endif()
if(BUILD_TESTING)
    add_executable(pcie_contract_probe tests/pcie_contract_probe.cc)
    target_link_libraries(pcie_contract_probe PRIVATE ndpsim_transport)
    add_test(NAME shared_pcie_contract COMMAND pcie_contract_probe)
endif()

# Independent controller buffer and arithmetic services; MQSim remains storage/link owner.
add_library(ndpsim_controller STATIC src/model/controller.cc)
set_target_properties(ndpsim_controller PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(ndpsim_controller PUBLIC ndpsim_transport ndpsim_operators)
target_compile_options(ndpsim_controller PRIVATE -Wall -Wextra)
target_link_libraries(ndpsim PRIVATE ndpsim_controller)
target_sources(ndpsim PRIVATE src/sst/controller_validation.cc)
if(BUILD_TESTING)
    add_executable(controller_probe tests/controller_probe.cc)
    target_include_directories(controller_probe PRIVATE tests "${CMAKE_BINARY_DIR}/generated")
    target_link_libraries(controller_probe PRIVATE ndpsim_controller)
    add_test(NAME controller_capacity COMMAND controller_probe)
    foreach(mode command staged evaluate result cross compute slots zero open-command query-staged)
        add_test(NAME controller_${mode} COMMAND controller_probe ${mode})
    endforeach()
    if(NOT NDPSIM_SANITIZE)
        add_test(NAME sst_controller_capacity COMMAND "${NDPSIM_SST_EXECUTABLE}" "--add-lib-path=$<TARGET_FILE_DIR:ndpsim>"
            "${PROJECT_SOURCE_DIR}/configs/regression/controller.py")
        set_tests_properties(sst_controller_capacity PROPERTIES PASS_REGULAR_EXPRESSION "M3.3 PASS" TIMEOUT 60)
    endif()
endif()
if(BUILD_TESTING)
    add_executable(controller_resource_probe tests/controller_resource_probe.cc)
    target_link_libraries(controller_resource_probe PRIVATE ndpsim_controller)
    add_test(NAME controller_resources COMMAND controller_resource_probe)
endif()

if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    add_test(NAME sst_graph_placements COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tools/run_pair.py"
        --pair --config "${PROJECT_SOURCE_DIR}/configs/regression/placements-branch.json" --build "${PROJECT_BINARY_DIR}"
        --output "${PROJECT_BINARY_DIR}/m3.4-ctest")
    set_tests_properties(sst_graph_placements PROPERTIES PASS_REGULAR_EXPRESSION "M3.4 paired PASS" TIMEOUT 120)
endif()

if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    add_test(NAME sst_graph_concurrency_output COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tools/verify_concurrency.py"
        --quick --build "${PROJECT_BINARY_DIR}" --output "${PROJECT_BINARY_DIR}/m4.1-ctest")
    set_tests_properties(sst_graph_concurrency_output PROPERTIES PASS_REGULAR_EXPRESSION "M4.1 PASS" TIMEOUT 120)
    add_test(NAME acceptance_parameter_regression COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tools/verify_acceptance.py"
        --quick --build "${PROJECT_BINARY_DIR}" --output "${PROJECT_BINARY_DIR}/m4.2-ctest")
    set_tests_properties(acceptance_parameter_regression PROPERTIES PASS_REGULAR_EXPRESSION "M4.2 PASS" TIMEOUT 180)
endif()

if(BUILD_TESTING)
    add_executable(observation_lifecycle_probe tests/observation_lifecycle_probe.cc)
    target_link_libraries(observation_lifecycle_probe PRIVATE ndpsim_runtime)
    add_test(NAME observation_lifecycle COMMAND observation_lifecycle_probe)
endif()
if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    add_test(NAME sift1m_matrix COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tests/test_t6_matrix.py")
    add_test(NAME streaming_output COMMAND "${Python3_EXECUTABLE}" "${PROJECT_SOURCE_DIR}/tests/test_streaming_output.py")
endif()

# V03-1/2/3: contract, dynamic lifetime management and trusted operator mappings.
file(READ "${PROJECT_SOURCE_DIR}/schemas/ndpsim-ir-v1.schema.json" IR_SCHEMA)
file(READ "${PROJECT_SOURCE_DIR}/schemas/ndpsim-ir-v1.capabilities.json" IR_CATALOG)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    schemas/ndpsim-ir-v1.schema.json schemas/ndpsim-ir-v1.capabilities.json)
configure_file(src/ir/embedded.h.in generated/ir/embedded.h @ONLY)
add_library(ndpsim_ir STATIC src/ir/schema.cc src/ir/encode.cc src/ir/executor.cc src/ir/mapping.cc)
set_target_properties(ndpsim_ir PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(ndpsim_ir PUBLIC src "${PROJECT_SOURCE_DIR}/third_party/sst-core/external"
    PRIVATE "${PROJECT_BINARY_DIR}/generated")
target_link_libraries(ndpsim_ir PUBLIC ndpsim_algorithm ndpsim_runtime ndpsim_host ndpsim_controller ndpsim_search)
target_compile_options(ndpsim_ir PRIVATE -Wall -Wextra -Wpedantic)
add_executable(ir_validate src/ir/validate.cc)
target_link_libraries(ir_validate PRIVATE ndpsim_ir)
if(BUILD_TESTING)
    add_test(NAME ir_schema_catalog COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_ir_schema.py" --validator "$<TARGET_FILE:ir_validate>")
endif()
if(BUILD_TESTING)
    add_executable(ir_schema_probe tests/ir_schema_probe.cc)
    target_link_libraries(ir_schema_probe PRIVATE ndpsim_ir)
    add_test(NAME ir_schema_memory COMMAND ir_schema_probe "${PROJECT_SOURCE_DIR}/examples/ir/graph-contract.json")
endif()
if(BUILD_TESTING)
    add_executable(ir_executor_probe tests/ir_executor_probe.cc)
    target_link_libraries(ir_executor_probe PRIVATE ndpsim_ir)
    add_test(NAME ir_dynamic_lifecycle COMMAND ir_executor_probe "${PROJECT_SOURCE_DIR}/examples/ir/graph-contract.json")
endif()
target_sources(ndpsim PRIVATE src/sst/ir_validation.cc)
target_link_libraries(ndpsim PRIVATE ndpsim_ir)
if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    foreach(variant base input compute return)
        add_test(NAME sst_ir_${variant} COMMAND "${NDPSIM_SST_EXECUTABLE}"
            "--add-lib-path=$<TARGET_FILE_DIR:ndpsim>" "${PROJECT_SOURCE_DIR}/configs/regression/ir.py")
        if(NOT variant STREQUAL "base")
            string(TOUPPER "${variant}" delay)
            set_tests_properties(sst_ir_${variant} PROPERTIES ENVIRONMENT "NDPSIM_IR_${delay}_DELAY=100")
        endif()
    endforeach()
endif()

if(BUILD_TESTING)
    add_executable(ir_mapping_probe tests/ir_mapping_probe.cc)
    target_include_directories(ir_mapping_probe PRIVATE "${CMAKE_BINARY_DIR}/generated")
    target_link_libraries(ir_mapping_probe PRIVATE ndpsim_ir)
    add_test(NAME ir_operator_placements COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_ir_mapping.py" --probe "$<TARGET_FILE:ir_mapping_probe>")
endif()

if(BUILD_TESTING)
    add_executable(ir_frontend_probe tests/ir_mapping_probe.cc)
    target_compile_definitions(ir_frontend_probe PRIVATE NDPSIM_PRODUCTION_FRONTEND=1)
    target_include_directories(ir_frontend_probe PRIVATE "${CMAKE_BINARY_DIR}/generated")
    target_link_libraries(ir_frontend_probe PRIVATE ndpsim_ir)
    add_test(NAME ir_graph_frontend COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_ir_mapping.py" --probe "$<TARGET_FILE:ir_frontend_probe>")
endif()

if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    add_test(NAME sst_ir_graph_frontend COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_ir_frontend.py" --build "${PROJECT_BINARY_DIR}")
    set_tests_properties(sst_ir_graph_frontend PROPERTIES TIMEOUT 240)
endif()
if(BUILD_TESTING)
    add_executable(ir_frontend_causal_probe tests/ir_frontend_causal_probe.cc)
    target_link_libraries(ir_frontend_causal_probe PRIVATE ndpsim_ir)
    add_test(NAME ir_graph_frontend_causality COMMAND ir_frontend_causal_probe
        "${PROJECT_SOURCE_DIR}/examples/ir/graph-contract.json")
endif()

if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    add_test(NAME ir_observation_comparator COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_ir_observation.py" --build "${PROJECT_BINARY_DIR}")
    set_tests_properties(ir_observation_comparator PROPERTIES TIMEOUT 240)
endif()

if(BUILD_TESTING)
    add_test(NAME ir_matrix_gates COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_ir_matrix.py")
endif()

if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    add_test(NAME v03_delivery_gates COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_v03_delivery.py")
    add_test(NAME v02_delivery_gates COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_v02_delivery.py")
endif()

if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    add_test(NAME v04_start_gates COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_v04_start.py")
    add_test(NAME v04_contract COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_v04_contract.py")
endif()
if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    add_test(NAME sst_static_replay COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_static_replay.py" "${PROJECT_BINARY_DIR}")
    set_tests_properties(sst_static_replay PROPERTIES TIMEOUT 60)
    add_test(NAME v04_validator COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_v04_validator.py" "${PROJECT_BINARY_DIR}")
    set_tests_properties(v04_validator PROPERTIES TIMEOUT 120)
endif()

if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    add_test(NAME v04_regression_gates COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_v04_regression.py")
endif()

if(BUILD_TESTING AND NOT NDPSIM_SANITIZE)
    add_test(NAME v04_delivery_gates COMMAND "${Python3_EXECUTABLE}"
        "${PROJECT_SOURCE_DIR}/tests/test_v04_delivery.py")
endif()
