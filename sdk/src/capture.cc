#include "ndpsim/capture.h"
#include <algorithm>
#include <limits>
#include <stdexcept>

namespace ndpsim::capture {
namespace {
using Fields = std::map<std::string,std::string>;
void require(bool ok, const char* message) { if (!ok) throw std::runtime_error(message); }
std::string number(std::uint64_t n) { return std::to_string(n); }
std::string quote(std::string_view s) {
    // Strict UTF-8 decoding, then ensure_ascii JSON, including surrogate pairs.
    std::string out="\"";
    auto escape=[&](std::uint32_t cp) {
        const char* hex="0123456789abcdef";
        out+="\\u";
        for (int n=12;n>=0;n-=4) out+=hex[(cp>>n)&15];
    };
    for (std::size_t i=0;i<s.size();) {
        auto c=static_cast<unsigned char>(s[i++]);std::uint32_t cp=c;
        if (c>=128) {
            unsigned continuation=0;
            if (c>=0xc2 && c<=0xdf) { continuation=1;cp=c&31; }
            else if (c>=0xe0 && c<=0xef) { continuation=2;cp=c&15; }
            else if (c>=0xf0 && c<=0xf4) { continuation=3;cp=c&7; }
            else throw std::runtime_error("invalid UTF-8");
            require(i+continuation<=s.size(),"truncated UTF-8");
            for (unsigned n=0;n<continuation;++n) { auto b=static_cast<unsigned char>(s[i++]);require((b&0xc0)==0x80,"invalid UTF-8 continuation");cp=(cp<<6)|(b&63); }
            require(cp>=(continuation==1?0x80u:continuation==2?0x800u:0x10000u) && cp<=0x10ffff && !(cp>=0xd800 && cp<=0xdfff),"noncanonical UTF-8");
        }
        if (cp=='"' || cp=='\\') { out+='\\';out+=char(cp); }
        else if (cp=='\b') out+="\\b";
        else if (cp=='\f') out+="\\f";
        else if (cp=='\n') out+="\\n";
        else if (cp=='\r') out+="\\r";
        else if (cp=='\t') out+="\\t";
        else if (cp<32 || cp>=127) {
            if (cp<=0xffff) escape(cp);
            else { cp-=0x10000;escape(0xd800+(cp>>10));escape(0xdc00+(cp&1023)); }
        } else out+=char(cp);
    }
    return out+'"';
}
std::string object(const Fields& fields) {
    std::string s="{";bool first=true;
    for (const auto& [k,v]:fields) { if (!first) s+=',';first=false;s+=quote(k)+':'+v; }
    return s+'}';
}
std::string array(const std::vector<std::string>& rows) {
    std::string s="[";bool first=true;
    for (const auto& row:rows) { if (!first) s+=',';first=false;s+=row; } return s+']';
}
template<class T, class F> std::string list(const std::vector<T>& rows,F f) {
    std::vector<std::string> result;result.reserve(rows.size());for (const auto& row:rows) result.push_back(f(row));return array(result);
}
std::string strings(const std::vector<std::string>& rows) { return list(rows,[](const auto& s){return quote(s);}); }
std::string quantities(const Counters& counters) {
    Fields fields;
    for (const auto& [name,q]:counters) fields.emplace(name,object({{"value",number(q.first)},{"unit",quote(q.second)}}));
    return object(fields);
}
std::string key(const Key& k) { return object({{"query",number(k.query)},{"op",quote(k.op)},{"stage",quote(k.stage)}}); }
std::string dependency(const Dependency& d) {
    const char* kind=nullptr;
    switch (d.kind) { case EdgeKind::Data:kind="DATA";break;case EdgeKind::Address:kind="ADDRESS";break;case EdgeKind::Control:kind="CONTROL";break;case EdgeKind::Order:kind="ORDER";break; }
    require(kind,"unknown dependency kind");
    return object({{"kind",quote(kind)},{"source",object({{"key",key(d.source)},{"port",quote(d.source_port)}})},{"input",quote(d.input)}});
}
std::string stage(const Stage& s) {
    return object({{"id",quote(s.id)},{"kind",quote(s.kind)},{"topological_rank",number(s.topological_rank)},
        {"dependencies",list(s.dependencies,dependency)},{"work",quantities(s.work)},{"features",quantities(s.features)},
        {"context",quote(s.context)},{"chunk",s.chunk?number(*s.chunk):"null"},{"segment",number(s.segment)},
        {"accesses",list(s.accesses,[](const Access& a){return object({{"object",quote(a.object)},{"version",number(a.version)},{"offset_bytes",number(a.offset_bytes)},{"length_bytes",number(a.length_bytes)}});})},
        {"outputs",list(s.outputs,[](const Output& o){return object({{"port",quote(o.port)},{"type",quote(o.type)},{"logical_bytes",number(o.logical_bytes)},{"version",number(o.version)},{"digest",quote(o.digest)}});})},
        {"state_versions",list(s.state_versions,[](const StateVersion& v){return object({{"name",quote(v.name)},{"before",number(v.before)},{"after",number(v.after)},{"input",v.input?quote(*v.input):"null"},{"output",quote(v.output)}});})},
        {"decomposition_rule",quote(s.decomposition_rule)}});
}
std::string operation(const Operation& o) {
    return object({{"query",number(o.query)},{"id",quote(o.id)},{"operator",quote(o.operator_id)},{"operator_version",quote(o.operator_version)},
                   {"semantic_work",quantities(o.semantic_work)},{"stages",list(o.stages,stage)}});
}
std::string units(const std::map<std::string,std::string>& values) { Fields f;for (const auto& [k,v]:values) f[k]=quote(v);return object(f); }
std::string port(const Port& p) {
    require(p.cardinality==Cardinality::One || p.cardinality==Cardinality::Many,"unknown port cardinality");
    return object({{"name",quote(p.name)},{"type",quote(p.type)},{"unit",quote(p.unit)},{"required",p.required?"true":"false"},{"cardinality",quote(p.cardinality==Cardinality::One?"one":"many")}});
}
std::string spec(const OperatorSpec& s) {
    return object({{"id",quote(s.id)},{"version",quote(s.version)},{"locations",strings(s.locations)},{"semantic_validator",quote(s.semantic_validator)},
        {"stages",list(s.stages,[](const StageSpec& st){return object({{"kind",quote(st.kind)},{"inputs",list(st.inputs,port)},{"outputs",list(st.outputs,port)},
            {"work_units",units(st.work_units)},{"feature_units",units(st.feature_units)},{"contexts",strings(st.contexts)},
            {"decomposition_rules",strings(st.decomposition_rules)},{"output_size_rule",quote(st.output_size_rule)}});})}});
}
std::string descriptor(const Object& o) {
    return object({{"id",quote(o.id)},{"version",number(o.version)},{"type",quote(o.type)},{"size_bytes",number(o.size_bytes)},
        {"shape",list(o.shape,[](auto n){return number(n);})},{"content_sha256",quote(o.content_sha256)}});
}
std::string provenance(const Provenance& p) {
    return object({{"kind",quote(p.kind)},{"capture_build",object({{"source_sha256",quote(p.capture_build.source_sha256)},
        {"binary_sha256",quote(p.capture_build.binary_sha256)},{"compiler",quote(p.capture_build.compiler)},{"flags",strings(p.capture_build.flags)}})},
        {"input_content_sha256",quote(p.input_content_sha256)},{"semantic_version",quote(p.semantic_version)},
        {"extension_sha256",quote(p.extension_sha256)},{"split",quote(p.split)},{"query_ids",list(p.query_ids,[](auto n){return number(n);})},
        {"workload",quote(p.workload)},{"numeric_version",quote(p.numeric_version)},{"work_version",quote(p.work_version)},{"semantic_config_sha256",quote(p.semantic_config_sha256)}});
}
void add_unique(std::vector<std::string>& keys,std::string k) { require(std::find(keys.begin(),keys.end(),k)==keys.end(),"duplicate metadata declaration");keys.push_back(std::move(k)); }
} // namespace
std::string encode_sample(const MeasurementSample& s) {
    require(s.calls>0 && s.end_cpu_ns>=s.start_cpu_ns && s.raw_ns==s.end_cpu_ns-s.start_cpu_ns,"invalid measurement interval");
    auto corrected=number(s.corrected_ns.magnitude);
    if (s.corrected_ns.negative && s.corrected_ns.magnitude) corrected="-"+corrected;
    return object({{"key",key(s.key)},{"chunk",s.chunk?number(*s.chunk):"null"},{"segment",number(s.segment)},
        {"stage_kind",quote(s.stage_kind)},{"work",quantities(s.work)},{"features",quantities(s.features)},
        {"calls",number(s.calls)},{"start_cpu_ns",number(s.start_cpu_ns)},{"end_cpu_ns",number(s.end_cpu_ns)},
        {"raw_ns",number(s.raw_ns)},{"corrected_ns",corrected},{"low_signal",s.low_signal?"true":"false"}})+'\n';
}
Recorder::Recorder(const std::filesystem::path& directory,Provenance p,Limits limits)
    :directory_(directory),provenance_(std::move(p)),limits_(limits) {
    require(limits.line_bytes && limits.line_bytes<=16*1024*1024 && limits.query_index_bytes && limits.query_index_bytes<=16*1024*1024 && limits.active_stages && limits.output_bytes,"invalid capture limits");
    require(!std::filesystem::exists(directory) && !std::filesystem::is_symlink(directory),"capture directory exists");
    require(std::filesystem::create_directory(directory),"cannot create capture directory");
    stream_.exceptions(std::ios::badbit|std::ios::failbit);stream_.open(directory/"operations.jsonl",std::ios::binary|std::ios::out);
}
Recorder::~Recorder() = default;
void Recorder::require_open() const { require(!closed_ && !failed_,"capture failed or closed"); }
void Recorder::declare_operator(const OperatorSpec& s) {
    std::lock_guard<std::mutex> lock(mutex_);
    try { require_open();require(!started_,"declaration after record");add_unique(operator_keys_,array({quote(s.id),quote(s.version)}));
        auto raw=spec(s);require(raw.size()+array(operators_).size()<=16*1024*1024,"operator metadata budget");operators_.push_back(std::move(raw)); }
    catch (...) { failed_=true;throw; }
}
void Recorder::declare_object(const Object& o) {
    std::lock_guard<std::mutex> lock(mutex_);
    try { require_open();require(!started_,"declaration after record");add_unique(object_keys_,array({quote(o.id),number(o.version)}));
        auto raw=descriptor(o);require(raw.size()+array(objects_).size()<=16*1024*1024,"object metadata budget");objects_.push_back(std::move(raw)); }
    catch (...) { failed_=true;throw; }
}
void Recorder::record(const Operation& o) {
    std::lock_guard<std::mutex> lock(mutex_);
    try { require_open();require(!operators_.empty(),"no operators declared");require(!o.stages.empty(),"operation without stages");
        require(std::find(provenance_.query_ids.begin(),provenance_.query_ids.end(),o.query)!=provenance_.query_ids.end(),"undeclared query");
        auto raw=operation(o)+'\n';require(raw.size()<=limits_.line_bytes && bytes_<=limits_.output_bytes && raw.size()<=limits_.output_bytes-bytes_,"capture record/output budget");
        started_=true;stream_.write(raw.data(),static_cast<std::streamsize>(raw.size()));bytes_+=raw.size();operations_hash_.update(raw); }
    catch (...) { failed_=true;throw; }
}
void Recorder::close(const std::vector<Key>& terminals) {
    std::lock_guard<std::mutex> lock(mutex_);
    try { require_open();require(started_ && !terminals.empty(),"empty/incomplete capture");stream_.close();
        auto raw=object({{"format",quote("ndpsim-capture/1")},{"complete","true"},{"lost_records","0"},
            {"operations_bytes",number(bytes_)},{"operations_sha256",quote(operations_hash_.hexdigest())},{"provenance",provenance(provenance_)},{"operators",array(operators_)},{"objects",array(objects_)},{"terminals",list(terminals,key)},
            {"limits",object({{"line_bytes",number(limits_.line_bytes)},{"query_index_bytes",number(limits_.query_index_bytes)},{"active_stages",number(limits_.active_stages)}})}})+'\n';
        require(raw.size()<=16*1024*1024 && bytes_<=limits_.output_bytes && raw.size()<=limits_.output_bytes-bytes_,"capture manifest/output budget");
        auto tmp=directory_/"capture.json.pending";std::ofstream metadata;metadata.exceptions(std::ios::badbit|std::ios::failbit);metadata.open(tmp,std::ios::binary);metadata.write(raw.data(),static_cast<std::streamsize>(raw.size()));metadata.close();
        std::filesystem::rename(tmp,directory_/"capture.json");closed_=true; }
    catch (...) { failed_=true;throw; }
}
} // namespace ndpsim::capture
