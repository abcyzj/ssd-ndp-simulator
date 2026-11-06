#include "ndpsim/capture.h"
#include <iostream>
#include <stdexcept>
using namespace ndpsim::capture;
int main(int argc,char** argv) {
    try {
        if (argc==2 && std::string(argv[1])=="--sample") {
            MeasurementSample sample;sample.key={7,"root","s0"};sample.stage_kind="reduce";
            sample.work={{"elements",{16,"count"}}};sample.start_cpu_ns=10;sample.end_cpu_ns=20;sample.raw_ns=10;
            sample.corrected_ns={true,2};sample.low_signal=true;std::cout<<encode_sample(sample);return 0;
        }
        if (argc==2 && std::string(argv[1])=="--hash") {
            Sha256 h;h.update("a");auto partial=h.hexdigest();h.update("bc");
            const auto abc="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
            if (h.hexdigest()!=abc || partial!=sha256("a")) return 1;
            for (unsigned n:{0,3,55,56,63,64,65,1000000}) {
                Sha256 chunked;std::string s(n,'a');for (std::size_t i=0;i<s.size();i+=17)chunked.update(std::string_view(s).substr(i,17));
                std::cout<<n<<' '<<chunked.hexdigest()<<'\n';
            }
            return 0;
        }
        if (argc<3 || argc>4) throw std::runtime_error("usage: capture_fixture NEW_DIRECTORY REGISTRY_SHA256 [budget-failure|unicode]");
        const std::string hash=sha256("synthetic fixture identity");
        Provenance p;
        p.kind="synthetic-fixture";p.capture_build={hash,hash,"fixture-cxx17",{}};
        p.input_content_sha256=hash;p.semantic_version="fixture/1";p.extension_sha256=argv[2];p.split="fixture";
        p.query_ids={7};p.workload="fixture";p.numeric_version="metadata/1";p.work_version="counts/1";p.semantic_config_sha256=hash;
        Limits limits;if (argc==4 && std::string(argv[3])=="budget-failure")limits.output_bytes=1;
        Recorder writer(argv[1],p,limits);
        Port input{"in0","fixture.u64","B",false,Cardinality::One};Port input2=input;input2.name="in1";
        Port output{"out","fixture.u64","B",true,Cardinality::One};
        StageSpec s; s.kind="reduce";s.inputs={input,input2};s.outputs={output};s.work_units={{"elements","count"}};
        s.contexts={"fixture/1"};s.decomposition_rules={"none/1"};s.output_size_rule="fixture.fixed-eight/1";
        OperatorSpec spec{"fixture.reduce","1",{"host","controller"},{s},"fixture-independent/1"};writer.declare_operator(spec);
        writer.declare_object({"block",0,"u32-le",64,{16},hash});
        const std::string root=argc==4 && std::string(argv[3])=="unicode"?u8"根😀\x7f":"root";
        std::vector<std::string> names={root,"left","right","join"};
        // Deliberately reorder physical records. All dependencies are supplied explicitly.
        for (int i:{3,1,0,2}) {
            Operation op;op.query=7;op.id=names[i];op.operator_id=spec.id;op.operator_version=spec.version;
            op.semantic_work={{"elements",{16,"count"}}};Stage st;st.id="s0";st.kind="reduce";st.topological_rank=i;st.context="fixture/1";
            st.work=op.semantic_work;st.outputs={{"out","fixture.u64",8,0,hash}};st.accesses={{"block",0,0,64}};
            if (i==1)st.dependencies={{EdgeKind::Data,{7,root,"s0"},"out","in0"}};
            if (i==2)st.dependencies={{EdgeKind::Address,{7,root,"s0"},"out","in0"}};
            if (i==3)st.dependencies={{EdgeKind::Control,{7,"left","s0"},"out","in0"},{EdgeKind::Order,{7,"right","s0"},"out","in1"}};
            op.stages={st};writer.record(op);
        }
        writer.close({{7,"join","s0"}});return 0;
    } catch (const std::exception& exc) { std::cerr<<exc.what()<<'\n';return 1; }
}
