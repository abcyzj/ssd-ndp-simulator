"""Generate physical layout from IR object metadata; never opens payload files.

This helper has no device allocation, build, collection or simulation side effects.
The C++ Layout loader is authoritative for mapping/range validation.
"""
U64 = 2**64 - 1


def uint(value, label):
    if type(value) is not int or not 0 <= value <= U64:
        raise ValueError(label + ': expected uint64')
    return value


def packed_layout(objects, *, capacity_sectors, page_bytes=4096,
                  alignment_bytes=512, start_lba=0, namespace='ssd0'):
    """Pack complete objects in supplied order, aligned independently.

    Shape/type/digest are IR identity, not physical geometry. Logical size is
    used verbatim (no fvecs headers, adjacency degree guesses or payload reads).
    A zero-size object has no extents and consumes no sectors.
    """
    capacity_sectors = uint(capacity_sectors, 'capacity_sectors')
    page_bytes = uint(page_bytes, 'page_bytes')
    alignment_bytes = uint(alignment_bytes, 'alignment_bytes')
    cursor = uint(start_lba, 'start_lba') * 512
    capacity = capacity_sectors * 512
    if not capacity_sectors or capacity > U64 or cursor > capacity:
        raise ValueError('namespace capacity/overflow')
    if page_bytes < 512 or page_bytes & (page_bytes - 1):
        raise ValueError('page_bytes must be power of two >=512')
    if not alignment_bytes or alignment_bytes & (alignment_bytes - 1):
        raise ValueError('alignment_bytes must be power of two')
    if not isinstance(namespace, str) or not 1 <= len(namespace.encode()) <= 256:
        raise ValueError('namespace id')
    rows, seen = [], set()
    for obj in objects:
        name, version, size = obj['id'], uint(obj['version'], 'version'), uint(obj['size_bytes'], 'size_bytes')
        if not isinstance(name, str) or not 1 <= len(name.encode()) <= 256 or (name, version) in seen:
            raise ValueError('invalid/duplicate object')
        seen.add((name, version))
        extents = []
        if size:
            cursor = (cursor + alignment_bytes - 1) // alignment_bytes * alignment_bytes
            end = cursor + size
            if end > capacity or end > U64:
                raise ValueError('layout exceeds namespace')
            extents.append(dict(logical_offset_bytes=0, physical_offset_bytes=cursor, length_bytes=size))
            cursor = end
        rows.append(dict(object=name, version=version, extents=extents))
    return dict(format='ndpsim-layout/1', namespace=dict(id=namespace, sector_bytes=512,
                page_bytes=page_bytes, capacity_sectors=capacity_sectors),
                alignment_bytes=alignment_bytes, objects=rows)
