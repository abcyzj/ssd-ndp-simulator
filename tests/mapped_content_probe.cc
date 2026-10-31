#include "content/content.h"
#include <fstream>
#include <filesystem>
#include <iostream>
#include <unistd.h>
using namespace ndpsim::content;
void require(bool ok) {
    if (!ok)
        throw std::runtime_error("mapped assertion failed");
}
template <class F> void reject(Code code, F f) {
    try {
        f();
    } catch (const Error& e) {
        require(e.code == code);
        return;
    }
    throw std::runtime_error("expected rejection");
}
int main() {
    auto dir =
        std::filesystem::temp_directory_path() / ("ndpsim-mapped-" + std::to_string(getpid()));
    std::filesystem::create_directory(dir);
    try {
        Bytes vectors(8 * 260 * 4, 0), graph(8 * 3 * 4, 0);
        std::vector<Input> objects;
        for (unsigned id = 0; id < 8; ++id) {
            // FP32 1.0 at first dimension; degrees vary and unused slots stay zero.
            vectors[id * 1040 + 2] = 128;
            vectors[id * 1040 + 3] = 63;
            graph[id * 12] = id % 3;
            for (unsigned j = 1; j <= id % 3; ++j)
                graph[id * 12 + j * 4] = (id + j) % 8;
            objects.push_back(
                {{1, Kind::Vector, id, 1},
                 Bytes(vectors.begin() + id * 1040, vectors.begin() + (id + 1) * 1040)});
            objects.push_back(
                {{1, Kind::Adjacency, id, 1},
                 Bytes(graph.begin() + id * 12, graph.begin() + id * 12 + (id % 3 + 1) * 4)});
        }
        auto write = [](const auto& path, const Bytes& bytes) {
            std::ofstream f(path, std::ios::binary);
            f.write(reinterpret_cast<const char*>(bytes.data()), bytes.size());
        };
        write(dir / "v", vectors);
        write(dir / "a", graph);
        ContentStore mapped((dir / "v").string(), (dir / "a").string(), 8, 260, 2),
            memory(std::move(objects));
        auto ann = ann_layout(memory, 1, 1, 8, 260, 2);
        auto compact = ann_layout(mapped, 1, 1, 8, 260, 2, 0, true);
        require(compact.placements.empty() && compact.end_lba == ann.end_lba);
        Layout old(memory, ann.end_lba, ann.placements),
            formula(mapped, compact.end_lba, compact, 8);
        Handles mh(mapped), rh(memory);
        for (unsigned id = 0; id < 8; ++id)
            for (auto kind : {Kind::Vector, Kind::Adjacency}) {
                ObjectRef ref{1, kind, id, 1};
                auto m = mapped.metadata(ref), r = memory.metadata(ref);
                require(m.size == r.size && m.sha256 == r.sha256);
                std::vector<Selection> selections{{ref, {0, m.size}}};
                auto mp = formula.resolve(selections), rp = old.resolve(selections);
                require(mp.extents.size() == rp.extents.size() &&
                        mp.extents[0].lba == rp.extents[0].lba &&
                        mp.sector_bytes == rp.sector_bytes);
                auto a = mh.create(selections, Location::HostMemory, {1, 1, 0});
                auto b = rh.create(selections, Location::HostMemory, {1, 1, 0});
                reject(Code::NotReady, [&] {
                    mh.try_get(a, Consumer::Algorithm, 1);
                });
                mh.publish(a);
                rh.publish(b);
                require(mh.try_get(a, Consumer::Algorithm, 1) ==
                        rh.try_get(b, Consumer::Algorithm, 1));
                mh.release(a);
                rh.release(b);
            }
        reject(Code::OutOfRange, [&] {
            mapped.metadata({1, Kind::Vector, 8, 1});
        });
        reject(Code::VersionMismatch, [&] {
            mapped.metadata({1, Kind::Vector, 0, 2});
        });
        reject(Code::Overflow, [&] {
            ann_layout(mapped, 1, 1, UINT32_MAX, UINT32_MAX, UINT32_MAX, UINT64_MAX, true);
        });
        require(sha256(Bytes{}) ==
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
        require(sha256(Bytes{'a', 'b', 'c'}) ==
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        mapped.verify_identity();
        vectors[0] = 1;
        write(dir / "v", vectors);
        reject(Code::VersionMismatch, [&] {
            mapped.verify_identity();
        });
        vectors.pop_back();
        write(dir / "short", vectors);
        reject(Code::InvalidArgument, [&] {
            ContentStore bad((dir / "short").string(), (dir / "a").string(), 8, 260, 2);
        });
        write(dir / "v", Bytes(8 * 1040, 0));
        graph[0] = 3;
        write(dir / "a", graph);
        reject(Code::OutOfRange, [&] {
            ContentStore bad((dir / "v").string(), (dir / "a").string(), 8, 260, 2);
        });
        std::filesystem::remove_all(dir);
        std::cout << "PASS mapped/memory bytes, hashes, formula layout, pending visibility, "
                     "immutable identity, errors\n";
    } catch (...) {
        std::filesystem::remove_all(dir);
        throw;
    }
}
