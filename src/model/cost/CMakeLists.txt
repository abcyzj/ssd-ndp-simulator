# Explicit JSON header dependency; no SST/MQSim libraries or functional operators.
add_library(ndpsim_cost STATIC cost_model.cc)
add_library(ndpsim::cost ALIAS ndpsim_cost)
set_target_properties(ndpsim_cost PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(ndpsim_cost PUBLIC "${PROJECT_SOURCE_DIR}/src"
    PRIVATE "${PROJECT_SOURCE_DIR}/third_party/sst-core/external" "${PROJECT_BINARY_DIR}/generated")
target_link_libraries(ndpsim_cost PUBLIC ndpsim::capture)
target_compile_options(ndpsim_cost PRIVATE -Wall -Wextra -Wpedantic -fno-fast-math -ffp-contract=off)
if(NDPSIM_SANITIZE)
    target_compile_options(ndpsim_cost PUBLIC -fsanitize=address -fno-omit-frame-pointer)
    target_link_options(ndpsim_cost PUBLIC -fsanitize=address)
endif()
