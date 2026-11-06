// Included after native Config/Input/Result. Actual v2 benchmark/capture/sample
// execution shares this function; legacy execution remains a migration reference.
namespace cap = ndpsim::capture;
struct GraphCaptureConfig {
    cap::Provenance provenance;
    std::vector<cap::Object> objects;
};
GraphCaptureConfig graph_capture_config;

class GraphCollector {
public:
    GraphCollector(const Config&, size_t query, Observation& options, bool capture, bool sample)
        : query(query), options(options), capture(capture), sample(sample) {
        if (capture) {
            auto p=graph_capture_config.provenance;p.query_ids={query};
            cap::Limits limits;limits.output_bytes=options.buffer_limit;
            writer=std::make_unique<cap::Recorder>(options.directory+"/"+std::to_string(query),p,limits);
            for(const auto& s:graph_specs())writer->declare_operator(s);
            for(const auto& o:graph_capture_config.objects)writer->declare_object(o);
        }
    }
    static cap::Counters work(const operators::Work& w) {
        cap::Counters out;
        for(size_t i=0;i<work_fields.size();++i)out[work_names[i]]={w.*work_fields[i],i==1?"flop":(i>=2 && i<=4?"B":"count")};
        return out;
    }
    cap::Operation operation(const char* name) {
        cap::Operation op;if(!capture && !sample)return op;op.query=query;op.id="op"+std::to_string(next_op++);
        op.operator_id=std::string("graph.")+name;op.operator_version="2";op.semantic_work=work({});return op;
    }
    cap::Stage stage(cap::Operation& op,const char* kind, const operators::Work& w,
                     std::initializer_list<std::pair<const char*,uint64_t>> features,
                     int64_t chunk=-1) {
        if(rank>=100000)throw std::runtime_error("graph query stage budget exceeded");
        cap::Stage s;s.id=std::string(kind)+"."+std::to_string(op.stages.size());s.kind=kind;
        s.segment=op.stages.size();s.topological_rank=rank++;s.context="resident_random/2";s.work=work(w);
        for(auto [k,v]:features)s.features[k]={v,std::string(k).find("bytes")!=std::string::npos?"B":"count"};
        if(chunk>=0)s.chunk=uint64_t(chunk);
        if(rank>1)s.dependencies.push_back({cap::EdgeKind::Order,previous,"out","in"});
        s.outputs.push_back({"out","token",0,0,content::sha256(Bytes{})});
        previous={query,op.id,s.id};return s;
    }
    void edge(cap::Stage& s,cap::Key key,const char* port,const char* input,cap::EdgeKind kind=cap::EdgeKind::Data) {
        s.dependencies.push_back({kind,key,port,input});
    }
    cap::Key key(const cap::Operation& op,const cap::Stage& s) {return {query,op.id,s.id};}
    void output(cap::Stage& s,const char* port,const char* type,const Bytes& bytes,uint64_t version=0) {
        s.outputs.push_back({port,type,bytes.size(),version,content::sha256(bytes)});
    }
    bool take(const char* kind) {return sample && counts[kind]++%options.stride==0;}
    void interval(const cap::Operation& op,const cap::Stage& st,uint64_t start,uint64_t end) {
        if(!start)return;
        cap::MeasurementSample s;s.key={query,op.id,st.id};s.chunk=st.chunk;s.segment=st.segment;s.stage_kind=st.kind;
        s.work=st.work;s.features=st.features;s.start_cpu_ns=start;s.end_cpu_ns=end;s.raw_ns=end-start;
        s.corrected_ns={false,s.raw_ns}; // final probe correction is bound by launcher after run.
        samples+=cap::encode_sample(s);
        if(samples.size()>options.buffer_limit)throw std::runtime_error("v2 sample buffer budget exceeded");
    }
    void record(cap::Operation& op) {if(capture)writer->record(op);}
    void close() {
        if(capture) {
            writer->close({previous});
            const auto dir=std::filesystem::path(options.directory)/std::to_string(query);
            uint64_t bytes=0;for(const auto& entry:std::filesystem::directory_iterator(dir))bytes+=entry.file_size();charge(bytes);
        }
        if(sample) {
            charge(samples.size());std::ofstream f(options.directory+"/"+std::to_string(query)+".jsonl",std::ios::binary);
            f<<samples;f.close();if(!f)throw std::runtime_error("v2 sample flush failed");
        }
    }
private:
    void charge(uint64_t n) {
        auto old=options.remaining.load();
        do {if(n>old)throw std::runtime_error("v2 total output budget exceeded");}
        while(!options.remaining.compare_exchange_weak(old,old-n));
    }
    uint64_t query,next_op=0,rank=0;
    Observation& options;bool capture,sample;
    cap::Key previous;
    std::unique_ptr<cap::Recorder> writer;
    std::map<std::string,size_t> counts;
    std::string samples;
};

template<Mode mode>
Result graph_query(const Config& c,const Input& input,size_t qi,Observation& options) {
    constexpr bool collect=mode==Mode::graph_capture || mode==Mode::graph_sample;
    constexpr bool sampling=mode==Mode::graph_sample;
    const auto qid=u32(input.query_ids.data()+4*qi);
    GraphCollector g(c,qid,options,mode==Mode::graph_capture,sampling);
    const auto tick=[](bool on){return sampling && on?clock_ns(CLOCK_THREAD_CPUTIME_ID):uint64_t{0};};
    Result result;cap::Key init_key,state_key,candidates_key,fresh_key;
    uint64_t state_version=0;size_t top_count=0;
    auto init_op=g.operation("INIT");bool probe=g.take("init");auto start=tick(probe);
    {
        const size_t stride=4*c.dimensions;
        const auto* ptr=input.queries.data()+qi*stride;const Bytes query(ptr,ptr+stride);
        std::set<uint32_t> scored,expanded;Bytes ranked,selected_payload;std::vector<uint32_t> fresh{uint32_t(c.entry)};
        auto end=tick(probe);
        if constexpr(collect) {
            auto s=g.stage(init_op,"init",{},{{"copied_bytes",stride}});
            s.accesses={{"queries",0,qi*stride,stride}};g.output(s,"query","graph.query",query);
            s.outputs.push_back({"state","graph.state",0,++state_version,content::sha256(Bytes{})});
            s.state_versions={{"query_state",0,state_version,std::nullopt,"state"}};
            init_key=state_key=g.key(init_op,s);g.interval(init_op,s,start,end);init_op.stages.push_back(s);g.record(init_op);
        }
        for(;;) {
            if(!fresh.empty()) {
                ++result.score_batches;
                auto mark=g.operation("MARK_SCORED");
                if constexpr(collect) {
                    auto s=g.stage(mark,"state",{},{{"objects",fresh.size()}});g.edge(s,state_key,"state","prior_state");
                    s.outputs.push_back({"state","graph.state",0,state_version+1,content::sha256(Bytes{})});
                    s.state_versions={{"query_state",state_version,state_version+1,std::string("prior_state"),"state"}};
                    ++state_version;state_key=g.key(mark,s);mark.stages.push_back(s);g.record(mark);
                }
                auto score=g.operation("SCORE_L2");Bytes batch;operators::Work semantic;
                for(size_t offset=0,chunk=0;offset<fresh.size();offset+=c.chunk,++chunk) {
                    auto limit=std::min(fresh.size(),offset+c.chunk);size_t n=limit-offset,old=batch.size()/12;
                    const bool ps=g.take("score_prepare"),pl=g.take("score_l2"),pa=g.take("merge_append");
                    auto a=tick(ps);uint64_t b,d,l2start,appendstart;operators::Work lw;
                    std::string vectors_digest;Bytes scored_payload;
                    {
                        std::vector<uint32_t> group(fresh.begin()+offset,fresh.begin()+limit);
                        std::vector<Bytes> vectors;vectors.reserve(group.size());
                        for(auto id:group){const auto* v=input.vectors.data()+id*stride;vectors.emplace_back(v,v+stride);scored.insert(id);}
                        b=tick(ps || pl);l2start=b;
                        auto value=operators::squared_l2(query,vectors,group);lw=value.work;
                        d=tick(pl || pa);appendstart=d;
                        batch.insert(batch.end(),value.bytes.begin(),value.bytes.end());
                        if constexpr(mode==Mode::graph_capture) {
                            cap::Sha256 hash;for(const auto& v:vectors)hash.update(std::string_view(reinterpret_cast<const char*>(v.data()),v.size()));
                            vectors_digest=hash.hexdigest();scored_payload=value.bytes;
                        }
                    }
                    auto appendend=tick(pa);
                    result.add(lw);add_work(semantic,lw);
                    if constexpr(collect) {
                        auto prep=g.stage(score,"score_prepare",{0,0,n*stride,n*stride,n*stride,0,0},{{"vectors",n},{"dimensions",c.dimensions},{"state_objects",n},{"copied_bytes",n*stride},{"decoded_bytes",0}},chunk);
                        g.edge(prep,init_key,"query","query");g.edge(prep,state_key,"state","state");
                        for(size_t j=offset;j<limit;++j)prep.accesses.push_back({"vectors",0,fresh[j]*stride,stride});
                        prep.outputs.push_back({"vectors","graph.vectors",n*stride,0,vectors_digest});
                        g.interval(score,prep,a,b);auto pk=g.key(score,prep);score.stages.push_back(prep);
                        auto l2=g.stage(score,"score_l2",lw,{{"vectors",n},{"dimensions",c.dimensions}},chunk);
                        g.edge(l2,init_key,"query","query");g.edge(l2,pk,"vectors","vectors");g.output(l2,"scores","graph.scores",scored_payload);l2.decomposition_rule="graph-l2-items/1";
                        g.interval(score,l2,l2start,d);auto lk=g.key(score,l2);score.stages.push_back(l2);
                        auto st=g.stage(score,"merge_append",{0,0,12*n,12*n,12*(old+n),0,0},{{"old_scores",old},{"new_scores",n},{"copied_bytes",12*n},{"released_vectors",n}},chunk);
                        g.edge(st,lk,"scores","scores");g.output(st,"batch","graph.scores",batch);
                        g.interval(score,st,appendstart,appendend);score.stages.push_back(st);
                    }
                    ++result.chunks;
                }
                if constexpr(collect){score.semantic_work=GraphCollector::work(semantic);g.record(score);}
                auto merge=g.operation("MERGE_CANDIDATES");auto old=ranked.size()/12;probe=g.take("merge_finalize");start=tick(probe);
                operators::Work mw;{
                    auto value=search::core::merge_candidates(std::move(ranked),batch,c.count);mw=value.work;ranked=std::move(value.bytes);Bytes{}.swap(batch);
                }end=tick(probe);result.add(mw);
                if constexpr(collect){auto st=g.stage(merge,"merge_finalize",mw,{{"old_scores",old},{"new_scores",fresh.size()}});
                    g.edge(st,g.key(score,score.stages.back()),"batch","batch");if(old)g.edge(st,candidates_key,"candidates","previous");
                    g.output(st,"candidates","graph.candidates",ranked);candidates_key=g.key(merge,st);g.interval(merge,st,start,end);merge.semantic_work=GraphCollector::work(mw);merge.stages.push_back(st);g.record(merge);}
            }
            auto select=g.operation("SELECT_FRONTIER");probe=g.take("select");start=tick(probe);
            auto selected=search::core::select_frontier(ranked,expanded,c.width,c.batch,c.budget);
            auto chosen=ids(selected.bytes.data(),selected.bytes.size()/4);selected_payload=std::move(selected.bytes);end=tick(probe);result.add(selected.work);
            cap::Key select_key;
            if constexpr(collect){auto st=g.stage(select,"select",selected.work,{{"old_scores",ranked.size()/12},{"expanded",expanded.size()},{"L",c.width},{"B",c.batch},{"E",c.budget}});
                g.edge(st,candidates_key,"candidates","candidates");g.output(st,"frontier","graph.frontier",selected_payload);select_key=g.key(select,st);g.interval(select,st,start,end);select.semantic_work=GraphCollector::work(selected.work);select.stages.push_back(st);g.record(select);}
            if(chosen.empty())break;
            ++result.rounds;auto mark=g.operation("MARK_EXPANDED");
            if constexpr(collect){auto st=g.stage(mark,"state",{},{{"objects",chosen.size()}});g.edge(st,state_key,"state","prior_state");g.edge(st,select_key,"frontier","frontier",cap::EdgeKind::Control);
                st.outputs.push_back({"state","graph.state",0,state_version+1,content::sha256(Bytes{})});st.state_versions={{"query_state",state_version,state_version+1,std::string("prior_state"),"state"}};
                ++state_version;state_key=g.key(mark,st);mark.stages.push_back(st);g.record(mark);}
            std::vector<std::vector<uint32_t>> rows;std::vector<cap::Key> reads;cap::Sha256 joined_hash;size_t joined_size=0;
            for(auto id:chosen){auto op=g.operation("READ_ADJACENCY");probe=g.take("adjacency_prepare");start=tick(probe);
                expanded.insert(id);auto row=input.graph.data()+id*(c.degree+1)*4;rows.push_back(ids(row+4,u32(row)));end=tick(probe);
                if constexpr(collect){auto n=rows.back().size();Bytes payload(row,row+4*(1+n));auto st=g.stage(op,"adjacency_prepare",{0,0,payload.size(),4*n,payload.size(),0,0},{{"edges",n},{"state_objects",1},{"copied_bytes",4*n},{"decoded_bytes",payload.size()}});
                    st.dependencies.front().source=state_key; // Independent row branches after the batch mark.
                    st.accesses={{"graph",0,id*(c.degree+1)*4,payload.size()}};g.edge(st,select_key,"frontier","frontier",cap::EdgeKind::Address);g.output(st,"neighbors","graph.neighbors",payload);g.interval(op,st,start,end);reads.push_back(g.key(op,st));op.stages.push_back(st);g.record(op);joined_hash.update(std::string_view(reinterpret_cast<const char*>(payload.data()),payload.size()));joined_size+=payload.size();}
            }
            auto join=g.operation("JOIN");cap::Key join_key;
            if constexpr(collect){auto st=g.stage(join,"join",{},{});for(const auto& key:reads)g.edge(st,key,"neighbors","neighbors");
                // References describe concatenation, not an extra storage read or copy.
                for(auto id:chosen)st.accesses.push_back({"graph",0,id*(c.degree+1)*4,4*(1+u32(input.graph.data()+id*(c.degree+1)*4))});
                st.outputs.push_back({"neighbors","graph.neighbors",joined_size,0,joined_hash.hexdigest()});join_key=g.key(join,st);join.stages.push_back(st);g.record(join);}
            auto dedup=g.operation("DEDUP_NEIGHBORS");probe=g.take("dedup");start=tick(probe);auto value=search::core::dedup_neighbors(rows,scored,c.count);
            fresh=ids(value.bytes.data(),value.bytes.size()/4);
            Bytes dedup_payload;if constexpr(mode==Mode::graph_capture)dedup_payload=value.bytes;
            Bytes{}.swap(value.bytes);decltype(rows){}.swap(rows);decltype(chosen){}.swap(chosen);
            end=tick(probe);result.add(value.work);
            if constexpr(collect){auto st=g.stage(dedup,"dedup",value.work,{{"edges",value.work.candidate_checks},{"scored",scored.size()}});g.edge(st,join_key,"neighbors","neighbors");g.edge(st,state_key,"state","state");g.output(st,"fresh","graph.frontier",dedup_payload);fresh_key=g.key(dedup,st);g.interval(dedup,st,start,end);dedup.semantic_work=GraphCollector::work(value.work);dedup.stages.push_back(st);g.record(dedup);}
        }
        probe=g.take("finish");start=tick(probe);ranked.resize(std::min(c.k,ranked.size()/12)*12);result.top=operators::scores(ranked);
        top_count=result.top.size();result.scored=scored.size();result.expanded=expanded.size();
    }
    auto end=tick(probe);
    if constexpr(collect){auto op=g.operation("FINISH");auto st=g.stage(op,"finish",{},{{"objects",top_count}});g.edge(st,candidates_key,"candidates","candidates");
        Bytes packed;for(const auto& score:result.top){
            for(size_t j=0;j<4;++j)packed.push_back(uint8_t(score.id>>(8*j)));
            uint64_t bits;std::memcpy(&bits,&score.distance,8);for(size_t j=0;j<8;++j)packed.push_back(uint8_t(bits>>(8*j)));}
        g.output(st,"topk","graph.scores",packed);g.interval(op,st,start,end);op.stages.push_back(st);g.record(op);}
    g.close();return result;
}
