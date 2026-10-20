#!/usr/bin/env python3
"""Observe only TCP 443 packet headers for 20 seconds; never print payload."""
import socket,struct,time
s=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x0800))
s.settimeout(1)
end=time.monotonic()+20
count=0
print('Watching TCP 443 headers for 20 seconds',flush=True)
while time.monotonic()<end:
 try:data=s.recv(128)
 except socket.timeout:continue
 if len(data)<54 or data[12:14]!=b'\x08\x00' or data[23]!=6:continue
 ihl=(data[14]&15)*4;offset=14+ihl
 if len(data)<offset+14:continue
 sport,dport=struct.unpack('!HH',data[offset:offset+4])
 if sport!=443 and dport!=443:continue
 src=socket.inet_ntoa(data[26:30]);dst=socket.inet_ntoa(data[30:34])
 if '8.138.230.183' not in (src,dst):continue
 flags=data[offset+13]
 if flags&0x07:
  count+=1;print(src,sport,'->',dst,dport,'flags',hex(flags),flush=True)
  if count>=30:break
print('Observed handshake/close headers:',count,flush=True)
s.close()
