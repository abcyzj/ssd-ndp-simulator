// Real payload and arithmetic live only in this standalone native program.
#include "contract.h"
#include <iostream>
#include <iterator>
using namespace ndpsim::capture;
int main(int argc, char** argv) {
    try {
        if (argc != 5)
            throw std::runtime_error(
                "usage: block_sum_native INPUT NEW_CAPTURE REGISTRY_SHA SOURCE_SHA");
        std::ifstream f(argv[1], std::ios::binary);
        if (!f)
            throw std::runtime_error("input missing");
        std::string raw((std::istreambuf_iterator<char>(f)), {});
        if (raw.size() != 32)
            throw std::runtime_error("exactly eight little endian u32 required");
        uint64_t a = 0, b = 0;
        for (size_t i = 0; i < 8; ++i) {
            uint32_t x = 0;
            for (size_t j = 0; j < 4; ++j)
                x |= uint32_t(uint8_t(raw[4 * i + j])) << (8 * j);
            (i < 4 ? a : b) += x;
        }
        std::ifstream bin(argv[0], std::ios::binary);
        std::string binary((std::istreambuf_iterator<char>(bin)), {});
        Provenance p;
        p.kind = "native-capture";
        p.capture_build = {
            argv[4], sha256(binary), __VERSION__, {"c++17", "-fno-fast-math", "-ffp-contract=off"}};
        p.input_content_sha256 = sha256(raw);
        p.semantic_version = "block-sum/1";
        p.extension_sha256 = argv[3];
        p.split = "tuning32";
        p.query_ids = {0};
        p.workload = "block-sum";
        p.numeric_version = "u32-le-u64/1";
        p.work_version = "element-count/1";
        p.semantic_config_sha256 = sha256("eight-u32-two-halves/1");
        Recorder writer(argv[2], p);
        writer.declare_operator(block_sum::contract());
        writer.declare_object({"block", 0, "u32-le", 32, {8}, sha256(raw)});
        // Physical file order deliberately differs from topological order.
        for (int i : {2, 1, 0}) {
            Operation op;
            op.query = 0;
            op.id = i == 0 ? "left" : i == 1 ? "right" : "join";
            op.operator_id = "example.BLOCK_SUM";
            op.operator_version = "1";
            Stage st;
            st.id = "sum";
            st.kind = "sum";
            st.topological_rank = i;
            st.context = "example-readonly/1";
            st.work = {{"elements", {i == 2 ? 2U : 4U, "count"}}};
            op.semantic_work = st.work;
            st.outputs = {{"out",
                           "example.u64",
                           8,
                           0,
                           sha256(std::to_string(i == 0   ? a
                                                 : i == 1 ? b
                                                          : a + b))}};
            if (i == 2)
                st.dependencies = {{EdgeKind::Data, {0, "left", "sum"}, "out", "left"},
                                   {EdgeKind::Data, {0, "right", "sum"}, "out", "right"}};
            else
                st.accesses = {{"block", 0, uint64_t(i * 16), 16}};
            op.stages = {st};
            writer.record(op);
        }
        writer.close({{0, "join", "sum"}});
        std::cout << "{\"left\":" << a << ",\"right\":" << b << ",\"join\":" << a + b << "}\n";
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
