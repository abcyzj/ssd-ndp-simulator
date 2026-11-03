#include "ir/schema.h"
#include <iostream>
int main(int argc, char** argv) {
    using namespace ndpsim::ir;
    try {
        if (argc == 2 && std::string(argv[1]) == "--schema")
            std::cout << schema().dump(2) << '\n';
        else if (argc == 2 && std::string(argv[1]) == "--capabilities")
            std::cout << capabilities().dump(2) << '\n';
        else if (argc == 2 && std::string(argv[1]) == "--catalog-hash")
            std::cout << catalog_sha256() << '\n';
        else if (argc == 2) {
            auto input = read_json(argv[1]);
            auto d = parse(input);
            validate(d);
            if (encode(d) != input)
                throw Error("InvalidArgument", "/", "typed representation differs from input");
            std::cout << Json({{"valid", true},
                               {"ir_version", d.manifest.ir_version},
                               {"execution_supported", false},
                               {"objects", d.objects.size()},
                               {"operations", d.operations.size()},
                               {"catalog_sha256", catalog_sha256()}})
                             .dump()
                      << '\n';
        } else {
            std::cerr << "usage: ir_validate FILE | --schema | --capabilities | --catalog-hash\n";
            return 2;
        }
        return 0;
    } catch (const Error& e) {
        std::cerr << e.diagnostic().dump() << '\n';
        return 1;
    } catch (const std::exception& e) {
        std::cerr << Json({{"code", "InvalidArgument"}, {"detail", e.what()}}).dump() << '\n';
        return 1;
    }
}
