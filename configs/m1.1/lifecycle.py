"""SST lifecycle-only smoke. Run with --add-lib-path=<platform build>."""
import argparse
import sst

parser = argparse.ArgumentParser()
parser.add_argument('--device-count', default='1')
parser.add_argument('--profile', default='native-small')
parser.add_argument('--components', type=int, default=1)
parser.add_argument('--unknown-param', action='store_true')
args = parser.parse_args()
sst.setProgramOption('timebase', '1ns')
for index in range(args.components):
    component = sst.Component('system'+str(index), 'ndpsim.System')
    component.addParams({'device_count': args.device_count, 'profile': args.profile})
    if args.unknown_param:
        component.addParam('unsupported_option', '1')
