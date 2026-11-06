#include "layout/layout.h"
#include <nlohmann/json.hpp>
#include <iostream>
#include <fstream>
using J = nlohmann::json;
int main(int argc, char** argv) {
    try {
        if (argc != 4)
            throw std::runtime_error("usage: probe reference declaring-file metadata-request");
        std::ifstream f(argv[3]);
        J j;
        f >> j;
        std::vector<ndpsim::capture::Object> objects;
        for (const auto& o : j.at("objects")) {
            ndpsim::capture::Object x;
            x.id = o.at("id");
            x.version = o.at("version");
            x.size_bytes = o.at("size_bytes");
            objects.push_back(x);
        }
        ndpsim::layout::Limits limits;
        if (j.contains("plan_budget"))
            limits.plan_sectors = j.at("plan_budget");
        if (j.contains("file_budget"))
            limits.file_bytes = j.at("file_budget");
        if (j.contains("extent_budget"))
            limits.extents = j.at("extent_budget");
        ndpsim::layout::Layout layout(argv[1], argv[2], objects, limits);
        std::vector<ndpsim::capture::Access> accesses;
        for (const auto& a : j.at("accesses"))
            accesses.push_back(
                {a.at("object"), a.at("version"), a.at("offset_bytes"), a.at("length_bytes")});
        auto p = layout.resolve(accesses);
        J out = {{"logical_bytes", p.logical_bytes},
                 {"sector_bytes", p.sector_bytes},
                 {"reads", J::array()},
                 {"source_path", layout.source_path().string()},
                 {"source_sha256", layout.source_sha256()},
                 {"source_bytes", layout.source_bytes()},
                 {"effective", J::parse(layout.effective_json())}};
        for (const auto& r : p.reads) {
            J slices = J::array();
            for (auto s : r.slices)
                slices.push_back({{"access_index", s.access_index},
                                  {"object_offset_bytes", s.object_offset_bytes},
                                  {"request_offset_bytes", s.request_offset_bytes},
                                  {"length_bytes", s.length_bytes}});
            out["reads"].push_back({{"lba", r.lba}, {"sectors", r.sectors}, {"slices", slices}});
        }
        std::cout << out.dump() << '\n';
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
