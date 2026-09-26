struct Object { float4 rect; float depth; uint id; uint valid; uint padding; };
StructuredBuffer<Object> objects : register(t0);
cbuffer Selection : register(b0) { uint object_index; };
cbuffer View : register(b1) { float width; float height; };
float4 vertex(uint vertex_id : SV_VertexID) : SV_Position {
    const uint corners[6]={0,1,2,2,1,3};
    uint c=corners[vertex_id];
    Object obj=objects[object_index];
    if (!all(isfinite(obj.rect)) || !isfinite(obj.depth) || obj.rect.x>=obj.rect.z || obj.rect.y>=obj.rect.w) return float4(2,2,0,1);
    float2 position=float2((c&1) ? obj.rect.z:obj.rect.x,(c&2) ? obj.rect.w:obj.rect.y);
    return float4(position.x/width*2-1,1-position.y/height*2,clamp(obj.depth,0,1),1);
}
float4 pixel() : SV_Target { return float4(1,1,1,1); }
