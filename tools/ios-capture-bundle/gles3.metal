// GLES 3.0 feature scenes for g5pdump `regions` (gles3 phase of
// docs/gpu/experiments/ios-capture-bundle.md).  Compiled to gles3.metallib
// by scripts/gpu/ios-capture-bundle-build.sh; driven by g5pdump-gles3.inc.m.
#include <metal_stdlib>
using namespace metal;

struct VOut {
    float4 pos [[position]];
    float2 uv;
};

// float4 positions in buffer 0 (the `pass` triangle layout); uv from xy.
vertex VOut v_pos(device const float4 *pos [[buffer(0)]], uint vid [[vertex_id]])
{
    VOut o;
    o.pos = pos[vid];
    o.uv = pos[vid].xy * 0.5f + 0.5f;
    return o;
}

// Instanced: the triangle scaled 0.4 and moved by off[instance_id].
vertex VOut v_inst(device const float4 *pos [[buffer(0)]],
                   device const float2 *off [[buffer(1)]],
                   uint vid [[vertex_id]], uint iid [[instance_id]])
{
    VOut o;
    float4 p = pos[vid];
    o.pos = float4(p.xy * 0.4f + off[iid], p.z, 1.0f);
    o.uv = float2(float(iid) * 0.25f, 0.0f);
    return o;
}

fragment float4 f_const(VOut in [[stage_in]], constant float4 &c [[buffer(0)]])
{
    return c;
}

fragment uint4 f_const_u(VOut in [[stage_in]], constant uint4 &c [[buffer(0)]])
{
    return c;
}

// Per-instance colour: instance 0..3 -> uv.x 0, .25, .5, .75.
fragment float4 f_inst(VOut in [[stage_in]])
{
    return float4(in.uv.x, 1.0f - in.uv.x, 0.5f, 1.0f);
}

constexpr sampler g3s(filter::nearest, address::clamp_to_edge);

fragment float4 f_sample(VOut in [[stage_in]], texture2d<float> t [[texture(0)]])
{
    return t.sample(g3s, in.uv);
}

fragment float4 f_read_u(VOut in [[stage_in]], texture2d<uint, access::read> t [[texture(0)]])
{
    uint4 v = t.read(uint2(in.pos.xy));
    return float4(v & 0xffu) / 255.0f;
}

fragment float4 f_array(VOut in [[stage_in]], texture2d_array<float> t [[texture(0)]])
{
    return t.sample(g3s, in.uv, 1);
}

fragment float4 f_3d(VOut in [[stage_in]], texture3d<float> t [[texture(0)]])
{
    return t.sample(g3s, float3(in.uv, 0.75f));
}

// Cube face 3 = -Y.
fragment float4 f_cube(VOut in [[stage_in]], texturecube<float> t [[texture(0)]])
{
    float2 c = in.uv * 2.0f - 1.0f;
    return t.sample(g3s, float3(c.x, -1.0f, c.y));
}
