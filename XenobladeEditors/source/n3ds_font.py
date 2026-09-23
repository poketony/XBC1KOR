"""Connect the verified 3DS font metadata writer to desktop fields."""
from font_metadata import remap_sparse,replace_widths
from xeno_formats import need


def edit_metadata(data,remap=None,width_edits=None):
    remap={k:v for k,v in (remap or {}).items() if k!=v}
    result=remap_sparse(data,remap) if remap else data
    widths={}
    for glyph,values in (width_edits or {}).items():
        need(len(values)==3 and all(type(v) is int for v in values),'Expected three integer widths')
        need(-128<=values[0]<=127 and 0<=values[1]<=255 and 0<=values[2]<=255,'Width field outside byte range')
        widths[glyph]=bytes(v&255 for v in values)
    return replace_widths(result,widths)
