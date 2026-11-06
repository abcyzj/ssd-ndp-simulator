"""P08 generic SST wiring fixture, with explicit metadata-only service demands."""
import os
import sst
node=sst.Component('dag','ndpsim_dag_validation.GraphHost')
node.addParams({'config':os.environ['NDPSIM_DAG_CONFIG']})
