#include "backend/lifecycle.h"
#include "sim/Engine.h"
#include "ssd/NVM_Channel_Base.h"
#include "host/IO_Flow_Base.h"
#include "exec/Device_Parameter_Set.h"
#include <cstdio>
#include <stdexcept>
#include <type_traits>

static_assert(std::has_virtual_destructor_v<MQSimEngine::Sim_Object>);
static_assert(std::has_virtual_destructor_v<SSD_Components::NVM_Channel_Base>);
static_assert(std::has_virtual_destructor_v<Host_Components::IO_Flow_Base>);

int main(int argc, char** argv) {
    try {
        const std::string mode = argc > 1 ? argv[1] : "normal";
        if (mode == "early") {
            { ndpsim::LifecycleBackend backend; }
            if (Simulator->GetObject("SSDDevice") || Simulator->Has_started()) return 11;
            std::puts("PASS: teardown before initialization clears device registry"); return 0;
        }
        if (mode == "invalid-mqsim") {
            char xml[] = "<Device_Parameter_Set><HostInterface_Type>INVALID</HostInterface_Type></Device_Parameter_Set>";
            rapidxml::xml_document<> doc;
            doc.parse<0>(xml);
            try { Device_Parameter_Set config; config.XML_deserialize(doc.first_node()); }
            catch (const std::runtime_error&) {
                std::puts("PASS: invalid MQSim XML configuration throws without reading stdin"); return 0;
            }
            return 12;
        }
        if (mode == "error") {
            try { PRINT_ERROR("deliberate library error " << 17) }
            catch (const std::runtime_error& error) {
                if (std::string(error.what()) != "deliberate library error 17") return 2;
                std::puts("PASS: PRINT_ERROR throws without reading stdin"); return 0;
            }
            return 3;
        }
        ndpsim::LifecycleBackend backend;
        try { backend.finish(); return 4; } catch (const std::logic_error&) {}
        backend.initialize();
        if (backend.time_ns() != 0 || !Simulator->Has_started()) return 5;
        try { backend.initialize(); return 6; } catch (const std::logic_error&) {}
        try { ndpsim::LifecycleBackend second; return 7; } catch (const std::logic_error&) {}
        backend.finish();
        backend.finish();
        if (Simulator->GetObject("SSDDevice") || Simulator->Has_started()) return 8;
        try { backend.initialize(); return 9; } catch (const std::logic_error&) {}
        try { ndpsim::LifecycleBackend reused; return 10; } catch (const std::logic_error&) {}
        std::puts("PASS: initialize/finish, transitions, single device and registry cleanup");
        return 0;
    } catch (const std::exception& error) { std::fprintf(stderr, "%s\n", error.what()); return 1; }
}
