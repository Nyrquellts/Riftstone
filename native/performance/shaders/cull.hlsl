struct Object { float4 rect; float depth; uint id; uint valid; uint padding; };
StructuredBuffer<Object> objects : register(t0);
StructuredBuffer<float> depths : register(t1);
RWByteAddressBuffer arguments : register(u0);
RWByteAddressBuffer count_buffer : register(u1);
cbuffer Params : register(b0) { uint width; uint height; uint count; uint levels; };
bool hidden(Object b) {
    bool result=b.valid!=0 && all(isfinite(b.rect)) && isfinite(b.depth) && b.depth>0.00001 && b.depth<=1
        && b.rect.x<b.rect.z && b.rect.y<b.rect.w && b.rect.z>0 && b.rect.w>0
        && b.rect.x<float(width) && b.rect.y<float(height);
    if (result) {
        uint2 start=(uint2)clamp(floor(b.rect.xy)-1,0,float2(width-1,height-1));
        uint2 end=(uint2)clamp(ceil(b.rect.zw),0,float2(width-1,height-1));
        uint w=width,h=height,offset=0,level=0;
        while (level+1<levels && max(end.x-start.x,end.y-start.y)>4) {
            start/=2;end/=2;offset+=w*h;w=(w+1)/2;h=(h+1)/2;level++;
        }
        for (uint y=start.y;y<=end.y;y++) for (uint x=start.x;x<=end.x;x++)
            result = result && (b.depth-0.00001>depths[offset+y*w+x]);
    }
    return result;
}
[numthreads(64,1,1)]
void main(uint3 dispatch_id : SV_DispatchThreadID) {
    uint index=dispatch_id.x;
    if (index>=count) return;
    Object object=objects[index];
    if (hidden(object)) return;
    uint slot=0;count_buffer.InterlockedAdd(0,1,slot);
    if (slot>=count) return; // Output capacity equals input count.
    uint base=slot*20;
    arguments.Store(base,index); // root object index, then D3D12_DRAW_ARGUMENTS
    arguments.Store4(base+4,uint4(6,1,0,0));
}
