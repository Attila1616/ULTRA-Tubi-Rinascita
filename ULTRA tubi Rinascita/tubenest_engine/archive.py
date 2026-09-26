"""ZIP packaging, checksum regeneration, validation and lossless repacking."""
from pathlib import Path, PurePosixPath
import hashlib
import zipfile
import xml.etree.ElementTree as ET
from .bcmp import Stream, FormatError


SECTIONS = ('Segments','Curves','Technical','LiteGeos','Layers','Portions','Shapes','Viewports')


def xml_bytes(root):
    ET.indent(root, space='\t')
    return b'<?xml version="1.0" encoding="utf-8"?>\r\n' + ET.tostring(root, encoding='utf-8', short_empty_elements=True).replace(b'\n',b'\r\n') + b'\r\n'


class Archive:
    def __init__(self, entries):
        self.entries = entries

    @classmethod
    def read(cls, path):
        with zipfile.ZipFile(path) as z:
            if len(z.namelist())!=len(set(z.namelist())):
                raise FormatError('Duplicate ZIP entry names')
            if sum(i.file_size for i in z.infolist())>256*1024*1024:
                raise FormatError('Archive exceeds the prototype 256 MiB limit')
            return cls({i.filename:z.read(i) for i in z.infolist()})

    def xml(self, name):
        return ET.fromstring(self.entries[name])

    def stream(self, section):
        return Stream.decode(self.entries[section+'/data.bin'])

    def refresh_checksums(self):
        for section in SECTIONS:
            path=section+'/content.xml'
            if path not in self.entries:
                continue
            data=self.entries.get(section+'/data.bin')
            if data is not None:
                root=self.xml(path)
                md5=root.find('MD5')
                if md5 is None:
                    md5=ET.SubElement(root,'MD5')
                md5.text=hashlib.md5(data).hexdigest().upper()
                self.entries[path]=xml_bytes(root)
            self.entries[section+'/sign']=hashlib.md5(self.entries[path]).digest()
        # Observed in BOTH fixtures; do not generalize this rule to other dialects.
        self.entries['sign']=hashlib.md5(self.entries['Curves/data.bin']).digest()

    def validate(self):
        checks=[]
        root=self.xml('content.xml')
        if root.get('DocType')!='NestResults3D' or root.get('FileVer')!='65542':
            raise FormatError('Unsupported ZZX document dialect')
        header=root.find('Header')
        try:
            handle_seed=int(header.get('HandleSeed')) if header is not None else None
        except (TypeError,ValueError):
            raise FormatError('Invalid root HandleSeed')
        records={}
        for section in SECTIONS:
            path=section+'/content.xml'
            content=self.entries[path]
            if self.entries.get(section+'/sign')!=hashlib.md5(content).digest():
                raise FormatError(f'{section}: XML signature mismatch')
            xr=self.xml(path)
            data=self.entries.get(section+'/data.bin')
            if data is not None:
                if xr.findtext('MD5','').upper()!=hashlib.md5(data).hexdigest().upper():
                    raise FormatError(f'{section}: binary MD5 mismatch')
                stream=Stream.decode(data)
                if stream.encode()!=data:
                    raise FormatError(f'{section}: binary round trip is not lossless')
                records[section]={r.address:r for r in stream.records}
            checks.append(section+': checksums and record framing OK')
        if self.entries.get('sign')!=hashlib.md5(self.entries['Curves/data.bin']).digest():
            raise FormatError('Root signature differs from observed Curves MD5 rule')
        shapes=self.xml('Shapes/content.xml')
        shape_handles={e.get('Handle') for e in shapes if e.tag!='MD5'}
        if len(shape_handles)!=len([e for e in shapes if e.tag!='MD5']):
            raise FormatError('Duplicate shape handles')
        for e in shapes:
            if e.tag=='MD5': continue
            copy_handle=e.get('CopyHandle')
            if copy_handle is not None:
                if copy_handle==e.get('Handle') or copy_handle not in shape_handles:
                    raise FormatError('Invalid Shape CopyHandle reference')
        for e in shapes:
            if e.tag=='MD5': continue
            self._ref(records,'Shapes',e,'DataAddr')
            rec=records['Shapes'][int(e.get('DataAddr'))]
            import struct
            obj=next(b for b in rec.blocks if b.name=='Object')
            if struct.unpack('<I',obj.payload)[0]!=int(e.get('Handle')):
                raise FormatError('Shape XML/binary handle mismatch')
            for g in e:
                if 'GeoAddr' in g.attrib:
                    self._ref(records,'LiteGeos',g,'GeoAddr')
                    target=records['LiteGeos'][int(g.get('GeoAddr'))]
                    if target.name!='TGe'+g.get('Class'):
                        raise FormatError('Geometry class/reference mismatch')
        segments=self.xml('Segments/content.xml')
        seg_handles=set()
        for s in segments.findall('TubeSegment'):
            if s.get('Handle') in seg_handles: raise FormatError('Duplicate segment handle')
            seg_handles.add(s.get('Handle'))
            self._ref(records,'Segments',s,'DataAddr')
            section=s.find('CrossSection')
            self._ref(records,'Curves',section,'DataAddr')
            self._ref(records,'LiteGeos',section.find('Geometry'),'GeoAddr')
            refs={r.get('Handle') for r in s.find('Shapes')}
            if not refs<=shape_handles or s.get('CutOffA') not in refs or s.get('CutOffB') not in refs:
                raise FormatError('Dangling shape/cutoff handle')
        viewport_handles={
            v.get('Handle')
            for v in self.xml('Viewports/content.xml').findall('.//VPort')
            if v.get('Handle') is not None
        }
        if len(viewport_handles)!=len([
            v for v in self.xml('Viewports/content.xml').findall('.//VPort')
            if v.get('Handle') is not None
        ]):
            raise FormatError('Duplicate viewport handles')

        for d in self.xml('Portions/content.xml').findall('DocPortion'):
            self._ref(records,'Portions',d,'DataAddr')
            pack=d.find('PackSegments')
            self._ref(records,'Segments',pack,'DataAddr')
            pack_record=records['Segments'][int(pack.get('DataAddr'))]
            pack_block=next((b for b in pack_record.blocks if b.name=='TubeSegments'),None)
            if pack_block is None or len(pack_block.payload)<8:
                raise FormatError('PackSegments has no usable TubeSegments payload')
            import math,struct
            pack_gap=struct.unpack_from('<d',pack_block.payload,0)[0]
            if not math.isfinite(pack_gap) or pack_gap<0:
                raise FormatError('Invalid PackSegments gap')
            listed=[s.get('Handle') for s in pack.findall('TubeSegment')]
            work=[s.get('Handle') for s in pack.findall('WorkSeq/Seg')]
            for handle in listed+work:
                if handle not in seg_handles: raise FormatError('Dangling segment handle')
            if listed!=work:
                raise FormatError('PackSegments WorkSeq differs from segment list')

        explicit_handles=[]
        for name,data in self.entries.items():
            if not name.endswith('content.xml'):
                continue
            try:
                xr=ET.fromstring(data)
            except ET.ParseError:
                continue
            for element in xr.iter():
                value=element.get('Handle')
                if value is not None:
                    try: explicit_handles.append(int(value))
                    except ValueError: raise FormatError('Non-integer XML handle')
        for section_records in records.values():
            for rec in section_records.values():
                obj=next((b for b in rec.blocks if b.name=='Object' and len(b.payload)>=4),None)
                if obj is not None:
                    explicit_handles.append(struct.unpack_from('<I',obj.payload,0)[0])
        # HandleSeed is not a strict max-handle invariant: several real,
        # TubesT-accepted source files retain viewport/object handles above it.
        # We only require it to be parseable when present.
        if handle_seed is None:
            raise FormatError('Missing root HandleSeed')

        checks.append('Root signature and geometry/object references OK')
        return checks

    @staticmethod
    def _ref(records,section,element,attribute):
        if element is None or int(element.get(attribute,'-1')) not in records.get(section,{}):
            raise FormatError(f'Invalid {section} {attribute} reference')

    def write(self,path):
        path=Path(path)
        if path.exists(): raise FileExistsError(f'Refusing to overwrite {path}')
        path.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED) as z:
            for name,data in self.entries.items():
                z.writestr(name,data)

    def extract(self,directory):
        directory=Path(directory).resolve()
        if directory.exists(): raise FileExistsError(directory)
        for name in self.entries:
            rel=PurePosixPath(name)
            dest=(directory/name).resolve()
            if rel.is_absolute() or '\\' in name or ':' in name or '..' in rel.parts or not dest.is_relative_to(directory):
                raise FormatError('Unsafe archive entry path')
        for name,data in self.entries.items():
            dest=directory/name
            if name.endswith('/'): dest.mkdir(parents=True,exist_ok=True)
            else:
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(data)
