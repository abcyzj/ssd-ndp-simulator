#pragma once
#include "transport/messages.h"
#include <map>
#include <set>

namespace ndpsim::transport {
// Endpoint protocol identities, separate from device credits/storage/computation.
// Call only on arrival. A rejected command can be answered with a charged Error envelope.
class Conversation {
    struct Session {
        uint64_t token = 0, version = 0;
        uint32_t dimension = 0;
        bool closing = false;
        std::set<uint64_t> chunks;
    };
    std::map<uint64_t, Session> sessions_;
    std::map<std::pair<uint64_t, uint64_t>, Message> pending_;
    std::set<uint64_t> tokens_;
    std::set<std::pair<uint64_t, uint64_t>> tasks_;
    static void check(bool ok, const char* why) {
        if (!ok)
            throw ProtocolError(why);
    }

public:
    void arrive(const Message& m) {
        auto key = std::make_pair(m.query, m.task);
        if (direction(m.kind) == Direction::H2D) {
            check(!tasks_.count(key), "reused command task");
            if (m.kind == Kind::QueryOpen) {
                check(!sessions_.count(m.query), "query already open");
                sessions_.emplace(m.query, Session{0, m.version, m.dimension, false, {}});
            } else {
                auto s = sessions_.find(m.query);
                check(s != sessions_.end() && s->second.token == m.token && m.token,
                      "query/token ownership mismatch");
                check(!s->second.closing, "query closing");
                if (m.kind == Kind::Score) {
                    check(s->second.dimension == m.dimension, "query dimension mismatch");
                    check(s->second.chunks.insert(m.chunk).second, "reused chunk ID");
                } else {
                    for (auto& p : pending_)
                        check(p.first.first != m.query, "close before all replies arrived");
                    s->second.closing = true;
                }
            }
            tasks_.insert(key);
            pending_.emplace(key, m);
            return;
        }
        auto p = pending_.find(key);
        check(p != pending_.end(), "orphan or duplicate reply");
        auto& request = p->second;
        auto s = sessions_.find(m.query);
        check(s != sessions_.end(), "missing reply session");
        if (m.kind == Kind::Error) {
            check(m.token == request.token, "error token mismatch");
            if (request.kind == Kind::QueryOpen)
                sessions_.erase(s);
            else if (request.kind == Kind::QueryClose)
                s->second.closing = false;
        } else if (request.kind == Kind::QueryOpen) {
            check(m.kind == Kind::QueryOpenAck && m.token && !tokens_.count(m.token),
                  "invalid OpenAck token/kind");
            s->second.token = m.token;
            tokens_.insert(m.token);
        } else if (request.kind == Kind::Score) {
            check(m.kind == Kind::ScoreResult && m.token == request.token &&
                      m.chunk == request.chunk && m.scores.size() == request.ids.size(),
                  "result identity/count mismatch");
            for (size_t i = 0; i < m.scores.size(); ++i)
                check(m.scores[i].id == request.ids[i], "result ID order mismatch");
        } else {
            check(m.kind == Kind::QueryCloseAck && m.token == request.token, "invalid CloseAck");
            sessions_.erase(s);
        }
        pending_.erase(p);
    }
    size_t pending() const {
        return pending_.size();
    }
    size_t sessions() const {
        return sessions_.size();
    }
};
} // namespace ndpsim::transport
