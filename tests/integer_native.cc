#include "integer_requests.h"
int main() { IntegerRequests requests; Simulator->AddObject(&requests); Simulator->Start_simulation(); return requests.completed==4?0:1; }
