"""Controlled profiles: native XML baseline plus frozen geometry overrides.

Static metadata estimate is an admission guard, not a measured RSS bound.
MQSim's actual namespace remains authoritative after construction.
"""
import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from ann_data import digest

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT/'configs/profiles/devices.json'
BASE = ROOT/'configs/native/ssdconfig.xml'
TAGS = dict(channels='Flash_Channel_Count', chips='Chip_No_Per_Channel', dies='Die_No_Per_Chip',
            planes='Plane_No_Per_Die', blocks_per_plane='Block_No_Per_Plane',
            pages_per_block='Page_No_Per_Block', page_bytes='Page_Capacity')


def profile(name):
    specs = json.loads(SPEC.read_text())
    if name not in specs: raise ValueError('Unsupported device profile: '+name)
    xml = BASE.read_text()
    root = ET.fromstring(xml)
    g = {k:int(root.find('.//'+tag).text) for k,tag in TAGS.items()}
    for key, value in specs[name].items():
        if key not in TAGS or type(value) is not int or value <= 0: raise ValueError('bad profile geometry')
        old = root.find('.//'+TAGS[key]).text
        xml = xml.replace('<'+TAGS[key]+'>'+old+'</'+TAGS[key]+'>', '<'+TAGS[key]+'>'+str(value)+'</'+TAGS[key]+'>')
        g[key] = value
    blocks = g['channels']*g['chips']*g['dies']*g['planes']*g['blocks_per_plane']
    pages = blocks*g['pages_per_block']
    raw = pages*g['page_bytes']
    if raw > 2**64-1 or g['page_bytes'] % 512: raise ValueError('profile geometry overflow/alignment')
    return dict(name=name, **g, raw_bytes=raw, page_sectors=g['page_bytes']//512,
                # Deliberately conservative proxy, excludes event history (T5).
                metadata_estimate_bytes=pages*256+blocks*4096, xml_sha256=hashlib.sha256(xml.encode()).hexdigest(),
                descriptor_sha256=digest(SPEC), baseline_sha256=digest(BASE), xml=xml)


def preflight(name, n, d, r, *, resource_budget_bytes=4*1024**3):
    p = profile(name)
    if any(type(x) is not int or not 0 < x <= 2**32-1 for x in (n,d)) or not 0 <= r < n:
        raise ValueError('invalid layout shape')
    vs, ads = (d*4+511)//512*512, ((r+1)*4+511)//512*512
    end = n*(vs+ads)//512
    preload = (end+p['page_sectors']-1)//p['page_sectors']*p['page_sectors']
    if preload > (2**64-1)//512: raise ValueError('layout/preload uint64 overflow')
    # Necessary raw limit before MQSim allocation; actual namespace is checked inside SST.
    if preload*512 > p['raw_bytes']: raise ValueError('layout/preload exceeds profile raw capacity')
    if type(resource_budget_bytes) is not int or resource_budget_bytes <= 0:
        raise ValueError('resource budget must be positive integer bytes')
    if p['metadata_estimate_bytes'] > resource_budget_bytes:
        raise ValueError('profile metadata estimate exceeds resource budget before MQSim allocation')
    return dict(profile={k:v for k,v in p.items() if k != 'xml'}, vector_stride=vs,
                adjacency_stride=ads, end_lba=end, preload_sectors=preload,
                resource_budget_bytes=resource_budget_bytes)


def header(path):
    lines = ['#pragma once', '#include <cstdint>', '#include <string>', '#include <stdexcept>', 'namespace ndpsim {',
             'inline constexpr char lifecycle_xml[] = R"mqsim('+BASE.read_text()+')mqsim";',
             'struct DeviceProfile { const char* name; const char* xml; uint64_t channels, chips, dies, planes, page_sectors, raw_sectors, metadata_estimate; const char* xml_sha256; };',
             'inline const DeviceProfile& device_profile(const std::string& name) {',
             'static const DeviceProfile profiles[] = {']
    for name in json.loads(SPEC.read_text()):
        p = profile(name)
        lines += ['{"'+name+'", R"mqsim('+p['xml']+')mqsim", '+', '.join(str(p[k]) for k in ('channels','chips','dies','planes','page_sectors'))+', '+str(p['raw_bytes']//512)+', '+str(p['metadata_estimate_bytes'])+', "'+p['xml_sha256']+'"},']
    lines += ['};', 'for (const auto& p : profiles) if (name == p.name) return p;',
              'throw std::invalid_argument("Unsupported device profile: " + name);', '}', '}']
    target = Path(path); content = '\n'.join(lines)+'\n'
    if not target.exists() or target.read_text() != content: target.write_text(content)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--header', required=True)
    header(parser.parse_args().header)
