#include "ssd/Host_Interface_Defs.h"
#include <iostream>

// Inspect the backend ABI used by PCIe_Root_Complex, not the NVMe specification.
int main()
{
    std::cout << "{\"sqe_bytes\":" << sizeof(Submission_Queue_Entry)
              << ",\"cqe_bytes\":" << sizeof(Completion_Queue_Entry)
              << ",\"doorbell_bytes\":" << sizeof(uint16_t) << "}\n";
    return 0;
}
