/* Dependency-free ZIP writer (stored entries, UTF-8 names). No network calls. */
(function(root){
 const table=Array.from({length:256},(_,n)=>{let c=n;for(let k=0;k<8;k++)c=(c&1)?0xedb88320^(c>>>1):c>>>1;return c>>>0});
 function crc(bytes){let c=0xffffffff;for(const b of bytes)c=table[(c^b)&255]^(c>>>8);return(c^0xffffffff)>>>0}
 function header(size){const bytes=new Uint8Array(size);return{bytes,view:new DataView(bytes.buffer)}}
 function create(entries){
  const chunks=[],central=[];let offset=0,centralSize=0;
  for(const entry of entries){
   if(!entry.name||entry.name.startsWith('/')||entry.name.split('/').includes('..'))throw Error('Invalid archive path');
   const name=new TextEncoder().encode(entry.name),data=entry.data,checksum=crc(data),local=header(30),v=local.view;
   v.setUint32(0,0x04034b50,true);v.setUint16(4,20,true);v.setUint16(6,0x800,true);v.setUint16(12,33,true);v.setUint32(14,checksum,true);v.setUint32(18,data.length,true);v.setUint32(22,data.length,true);v.setUint16(26,name.length,true);
   chunks.push(local.bytes,name,data);
   const c=header(46),w=c.view;w.setUint32(0,0x02014b50,true);w.setUint16(4,20,true);w.setUint16(6,20,true);w.setUint16(8,0x800,true);w.setUint16(14,33,true);w.setUint32(16,checksum,true);w.setUint32(20,data.length,true);w.setUint32(24,data.length,true);w.setUint16(28,name.length,true);w.setUint32(42,offset,true);
   central.push(c.bytes,name);centralSize+=46+name.length;offset+=30+name.length+data.length;
  }
  const end=header(22),v=end.view;v.setUint32(0,0x06054b50,true);v.setUint16(8,entries.length,true);v.setUint16(10,entries.length,true);v.setUint32(12,centralSize,true);v.setUint32(16,offset,true);
  return new Blob([...chunks,...central,end.bytes],{type:'application/zip'});
 }
 root.AnthemZip={create};
})(globalThis);
