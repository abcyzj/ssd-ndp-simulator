#pragma once
#include "ndpsim/capture.h"
namespace block_sum {
inline ndpsim::capture::OperatorSpec contract() {
    using namespace ndpsim::capture;
    StageSpec s;
    s.kind = "sum";
    s.inputs = {{"left", "example.u64", "B", false, Cardinality::One},
                {"right", "example.u64", "B", false, Cardinality::One}};
    s.outputs = {{"out", "example.u64", "B", true, Cardinality::One}};
    s.work_units = {{"elements", "count"}};
    s.contexts = {"example-readonly/1"};
    s.decomposition_rules = {"none/1"};
    s.output_size_rule = "example.eight/1";
    return {"example.BLOCK_SUM", "1", {"host", "controller"}, {s}, "example-independent-sum/1"};
}
} // namespace block_sum
