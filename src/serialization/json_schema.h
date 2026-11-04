#pragma once
#include <nlohmann/json.hpp>
#include <algorithm>
#include <cmath>
#include <regex>
#include <set>

namespace ndpsim::serialization {
using Json = nlohmann::json;
// The generated schema uses only this explicitly implemented JSON Schema subset.
// Tests independently validate fixtures with Draft202012Validator when available.
template <class Error, class Require>
void validate(
    const Json& v, const Json& s, const std::string& p, const Json& root, const Require& require) {
    if (s.contains("$ref")) {
        validate<Error>(v,
                        root.at(Json::json_pointer(s["$ref"].get<std::string>().substr(1))),
                        p,
                        root,
                        require);
        return;
    }
    if (s.contains("oneOf")) {
        // Tagged unions retain the selected branch's precise diagnostic.
        for (const auto& tag : {"operator", "ref", "kind"})
            if (v.is_object() && v.contains(tag)) {
                for (const auto& branch : s["oneOf"])
                    if (branch.contains("properties") && branch["properties"].contains(tag) &&
                        ((branch["properties"][tag].contains("const") &&
                          branch["properties"][tag]["const"] == v[tag]) ||
                         branch["properties"][tag].value("enum", Json::array()) ==
                             Json::array({v[tag]}))) {
                        validate<Error>(v, branch, p, root, require);
                        return;
                    }
            }
        size_t n = 0;
        for (const auto& branch : s["oneOf"]) {
            try {
                validate<Error>(v, branch, p, root, require);
                ++n;
            } catch (const Error&) {
            }
        }
        require(n == 1, p, "expected exactly one schema alternative");
        return;
    }
    if (s.contains("const"))
        require(v == s["const"],
                p,
                "unknown version, semantic constant or operator combination",
                "Unsupported");
    if (s.contains("enum"))
        require(std::find(s["enum"].begin(), s["enum"].end(), v) != s["enum"].end(),
                p,
                "unknown enum value",
                "Unsupported");
    if (!s.contains("type"))
        return;
    const auto t = s["type"].get<std::string>();
    if (t == "object") {
        require(v.is_object(), p, "expected object");
        if (s.contains("required"))
            for (const auto& k : s["required"])
                require(
                    v.contains(k.get<std::string>()), p, "missing field: " + k.get<std::string>());
        for (auto i = v.begin(); i != v.end(); ++i) {
            if (s.contains("properties") && s["properties"].contains(i.key()))
                validate<Error>(
                    i.value(), s["properties"][i.key()], p + "/" + i.key(), root, require);
            else
                require(s.value("additionalProperties", true),
                        p + "/" + i.key(),
                        "unknown semantic field",
                        "Unsupported");
        }
    } else if (t == "array") {
        require(v.is_array(), p, "expected array");
        require(v.size() >= s.value("minItems", size_t(0)), p, "array too short");
        if (s.contains("maxItems"))
            require(v.size() <= s["maxItems"].get<size_t>(), p, "array too long");
        if (s.value("uniqueItems", false))
            require(
                std::set<Json>(v.begin(), v.end()).size() == v.size(), p, "duplicate array item");
        for (size_t i = 0; i < v.size(); ++i)
            validate<Error>(v[i], s.at("items"), p + "/" + std::to_string(i), root, require);
    } else if (t == "string") {
        require(v.is_string(), p, "expected string");
        const auto str = v.get<std::string>();
        require(str.size() >= s.value("minLength", size_t(0)), p, "empty string");
        if (s.contains("maxLength"))
            require(str.size() <= s["maxLength"].get<size_t>(), p, "string too long");
        if (s.contains("pattern"))
            require(std::regex_match(str, std::regex(s["pattern"].get<std::string>())),
                    p,
                    "invalid identifier/hash");
    } else if (t == "boolean")
        require(v.is_boolean(), p, "expected boolean");
    else if (t == "integer") {
        require(v.is_number_integer() && !v.is_boolean(), p, "expected exact unsigned integer");
        require(v.is_number_unsigned() || v.get<int64_t>() >= 0, p, "negative integer");
        const auto n = v.get<uint64_t>();
        if (s.contains("minimum"))
            require(n >= s["minimum"].get<uint64_t>(), p, "integer below minimum", "OutOfRange");
        if (s.contains("maximum"))
            require(n <= s["maximum"].get<uint64_t>(), p, "integer above maximum", "OutOfRange");
    } else if (t == "number") {
        require(v.is_number() && std::isfinite(v.get<double>()), p, "expected finite number");
        if (s.contains("minimum"))
            require(v.get<double>() >= s["minimum"].get<double>(),
                    p,
                    "number below minimum",
                    "OutOfRange");
    } else
        require(false, p, "unimplemented schema keyword type", "Unsupported");
}
template <class Input, class Duplicate> Json parse_unique(Input& raw, const Duplicate& duplicate) {
    std::vector<std::set<std::string>> keys;
    auto callback = [&](int, Json::parse_event_t event, Json& parsed) {
        if (event == Json::parse_event_t::object_start)
            keys.emplace_back();
        if (event == Json::parse_event_t::key &&
            !keys.back().insert(parsed.get<std::string>()).second)
            duplicate(parsed.get<std::string>());
        if (event == Json::parse_event_t::object_end)
            keys.pop_back();
        return true;
    };
    return Json::parse(raw, callback);
}
} // namespace ndpsim::serialization
