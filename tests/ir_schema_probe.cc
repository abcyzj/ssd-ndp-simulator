#include "ir/schema.h"
#include <iostream>
using namespace ndpsim::ir;
void check(bool ok) {
    if (!ok)
        throw std::runtime_error("typed IR contract assertion failed");
}
int main(int argc, char** argv) {
    if (argc != 2)
        return 2;
    try {
        auto original = read_json(argv[1]);
        auto d = parse(original);
        check(d.manifest.ir_version == version && d.operations.size() == 7 &&
              d.objects.size() == 3);
        check(d.operations[0].op == Operator::QueryReady && d.operations[0].kind == Kind::External);
        check(d.operations[1].op == Operator::ScoreL2 && d.operations[1].kind == Kind::Compute);
        check(d.operations[4].op == Operator::ReadAdjacency &&
              d.operations[4].kind == Kind::Memory);
        check(d.operations[5].op == Operator::DedupNeighbors &&
              d.operations[5].kind == Kind::Control);
        check(d.operations[6].op == Operator::Join && d.operations[6].kind == Kind::Sync);
        check(d.operations[1].outputs[0].type.name == TypeName::ScoresIdF64);
        check(std::get<OutputRef>(d.operations[1].inputs[0].value.reference).producer_op == 1);
        check(std::get<std::vector<uint32_t>>(
                  std::get<Literal>(d.operations[1].inputs[1].value.reference).value) ==
              std::vector<uint32_t>{0});
        check(std::get<StateRef>(d.operations[3].inputs[1].value.reference).version == 1);
        check(d.operations[4].deps[0].kind == DependencyKind::Address);
        check(std::get<OutputRef>(d.operations[4].access[0].indices).producer_op == 4);
        check(encode(d) == original);
        validate(d);
        d.operations[1].op = static_cast<Operator>(999);
        try {
            validate(d);
            throw std::runtime_error("invalid memory enum accepted");
        } catch (const Error& e) {
            check(e.code == "Unsupported");
        }
        d = parse(original);
        d.objects[0].content_version = 0;
        try {
            validate(d);
            throw std::runtime_error("invalid memory version accepted");
        } catch (const Error& e) {
            check(e.code == "OutOfRange");
        }
        std::cout << "typed IR contract PASS\n";
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
