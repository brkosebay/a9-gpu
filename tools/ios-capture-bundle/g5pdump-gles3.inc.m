// GLES 3.0 capture scenes for `g5pdump regions` (gles3 phase,
// docs/gpu/experiments/ios-capture-bundle.md).  #included into g5pdump.m by
// g5pdump-gles3-scenes.patch, after the LOAD/MRT scenes.  LIB = gles3.metallib.
// One command buffer per scene (one submit), 64x64, CLEAR baseline g3-base:
//   g3-base           BGRA8 clear blue + f_const red triangle (the baseline)
//   msaa4-resolve     4x MSAA BGRA8, StoreActionMultisampleResolve into a 1x target
//   msaa4-store       4x MSAA BGRA8, StoreActionStore (no resolve)
//   depth-store       + Depth32Float clear 1.0, Less + write, stored
//   depth-load        depth-store, then pass 2 loads colour+depth, full screen z=.75 green, Less
//   depth16-load      the same with Depth16Unorm
//   stencil           Depth32Float_Stencil8: pass 1 writes ref 1 under the triangle,
//                     pass 2 loads it, full screen green with stencil Equal 1
//   fmt-*             pass 1 renders f_const(_u) into the format, pass 2 samples
//                     (float) or reads (uint) it full screen into BGRA8
//   tex-array/3d/cube 4x4 textures, CPU filled, sampled at layer 1 / slice 1 / face 3
//   occlusion         visibility result buffer (Counting) around the triangle draw
//   instanced         drawPrimitives instanceCount=4 with [[instance_id]]

typedef struct {
    const char *mode;
    MTLPixelFormat fmt;
    unsigned bpp;
    BOOL isUint;
} G3Fmt;

static const G3Fmt kG3Fmts[] = {
    { "fmt-srgb8", MTLPixelFormatRGBA8Unorm_sRGB, 4, NO },
    { "fmt-rgba16f", MTLPixelFormatRGBA16Float, 8, NO },
    { "fmt-r32f", MTLPixelFormatR32Float, 4, NO },
    { "fmt-rg16f", MTLPixelFormatRG16Float, 4, NO },
    { "fmt-rgba8ui", MTLPixelFormatRGBA8Uint, 4, YES },
    { "fmt-r32ui", MTLPixelFormatR32Uint, 4, YES },
    { "fmt-rgb10a2", MTLPixelFormatRGB10A2Unorm, 4, NO },
    { "fmt-r8", MTLPixelFormatR8Unorm, 1, NO },
    { "fmt-rg8", MTLPixelFormatRG8Unorm, 2, NO },
};

static const G3Fmt *g3Fmt(NSString *m)
{
    for (size_t i = 0; i < sizeof(kG3Fmts) / sizeof(kG3Fmts[0]); i++)
        if ([m isEqualToString:@(kG3Fmts[i].mode)])
            return &kG3Fmts[i];
    return NULL;
}

static NSArray<NSString *> *gles3Modes(void)
{
    NSMutableArray *a = [@[ @"g3-base", @"msaa4-resolve", @"msaa4-store", @"depth-store",
                            @"depth-load", @"depth16-load", @"stencil", @"tex-array", @"tex-3d",
                            @"tex-cube", @"occlusion", @"instanced" ] mutableCopy];
    for (size_t i = 0; i < sizeof(kG3Fmts) / sizeof(kG3Fmts[0]); i++)
        [a addObject:@(kG3Fmts[i].mode)];
    return a;
}

static BOOL isGles3Mode(NSString *m) { return [gles3Modes() containsObject:m]; }

static const float kG3Tri[3][4] = { { -.75f, -.75f, .5f, 1 }, { .75f, -.75f, .5f, 1 }, { 0, .75f, .5f, 1 } };
static const float kG3Full[3][4] = { { -1, -1, .75f, 1 }, { 3, -1, .75f, 1 }, { -1, 3, .75f, 1 } };
static const float kG3Red[4] = { 1, 0, 0, 1 };
static const float kG3Green[4] = { 0, 1, 0, 1 };

static id<MTLRenderPipelineState> g3Pso(id<MTLDevice> dev, id<MTLLibrary> lib, const char *vsName,
                                        const char *fsName, MTLPixelFormat color,
                                        MTLPixelFormat depth, MTLPixelFormat stencil,
                                        NSUInteger samples, NSMutableString *info)
{
    id<MTLFunction> vs = [lib newFunctionWithName:@(vsName)];
    id<MTLFunction> fs = [lib newFunctionWithName:@(fsName)];
    if (!vs || !fs) {
        [info appendFormat:@"missing %s or %s (LIB must be gles3.metallib)\n", vsName, fsName];
        return nil;
    }
    MTLRenderPipelineDescriptor *d = [MTLRenderPipelineDescriptor new];
    d.vertexFunction = vs;
    d.fragmentFunction = fs;
    d.colorAttachments[0].pixelFormat = color;
    d.depthAttachmentPixelFormat = depth;
    d.stencilAttachmentPixelFormat = stencil;
    d.rasterSampleCount = samples;
    NSError *err = nil;
    id<MTLRenderPipelineState> p = [dev newRenderPipelineStateWithDescriptor:d error:&err];
    if (!p)
        [info appendFormat:@"%s+%s: no pipeline (%@)\n", vsName, fsName, err.localizedDescription];
    else
        [info appendFormat:@"pso %s + %s color=%lu depth=%lu stencil=%lu samples=%lu class=%s\n",
                           vsName, fsName, (unsigned long)color, (unsigned long)depth,
                           (unsigned long)stencil, (unsigned long)samples,
                           class_getName([(NSObject *)p class])];
    return p;
}

static id<MTLTexture> g3Tex(id<MTLDevice> dev, MTLTextureType type, MTLPixelFormat fmt,
                            NSUInteger w, NSUInteger h, NSUInteger depthOrLayers,
                            NSUInteger samples, MTLStorageMode sm, MTLTextureUsage usage,
                            NSString *label, NSMutableArray *keep, NSMutableString *info)
{
    MTLTextureDescriptor *td = [MTLTextureDescriptor new];
    td.textureType = type;
    td.pixelFormat = fmt;
    td.width = w;
    td.height = h;
    if (type == MTLTextureType3D)
        td.depth = depthOrLayers;
    else if (type == MTLTextureType2DArray)
        td.arrayLength = depthOrLayers;
    td.sampleCount = samples;
    td.storageMode = sm;
    td.usage = usage;
    id<MTLTexture> t = [dev newTextureWithDescriptor:td];
    if (!t) {
        [info appendFormat:@"%@: texture allocation failed fmt=%lu type=%lu\n", label,
                           (unsigned long)fmt, (unsigned long)type];
        return nil;
    }
    t.label = label;
    [keep addObject:t];
    noteTexture(info, label, t);
    [info appendFormat:@"  %@ type=%lu samples=%lu storage=%lu usage=%lu\n", label,
                       (unsigned long)type, (unsigned long)samples, (unsigned long)sm,
                       (unsigned long)usage];
    return t;
}

static id<MTLBuffer> g3Buf(id<MTLDevice> dev, const void *p, NSUInteger len, NSString *what,
                           NSMutableArray *keep, NSMutableString *info)
{
    id<MTLBuffer> b = [dev newBufferWithBytes:p length:len options:MTLResourceStorageModeShared];
    [keep addObject:b];
    noteBuffer(info, what, b);
    return b;
}

static id<MTLDepthStencilState> g3Dss(id<MTLDevice> dev, BOOL depth, MTLCompareFunction stCmp,
                                      MTLStencilOperation stPass)
{
    MTLDepthStencilDescriptor *d = [MTLDepthStencilDescriptor new];
    d.depthCompareFunction = depth ? MTLCompareFunctionLess : MTLCompareFunctionAlways;
    d.depthWriteEnabled = depth;
    if (stCmp != MTLCompareFunctionAlways || stPass != MTLStencilOperationKeep) {
        MTLStencilDescriptor *s = [MTLStencilDescriptor new];
        s.stencilCompareFunction = stCmp;
        s.depthStencilPassOperation = stPass;
        s.readMask = 0xff;
        s.writeMask = 0xff;
        d.frontFaceStencil = s;
        d.backFaceStencil = s;
    }
    return [dev newDepthStencilStateWithDescriptor:d];
}

// 4x4 RGBA8 texel block: texel (x,y) of layer/slice/face L = x*60 | y*60<<8 | L*40<<16 | ff<<24
static void g3Fill(id<MTLTexture> t, NSUInteger layers, BOOL is3D)
{
    uint32_t px[16];
    for (NSUInteger l = 0; l < layers; l++) {
        for (unsigned y = 0; y < 4; y++)
            for (unsigned x = 0; x < 4; x++)
                px[y * 4 + x] = (x * 60u) | ((y * 60u) << 8) | (((unsigned)l * 40u) << 16) | 0xff000000u;
        if (is3D)
            [t replaceRegion:MTLRegionMake3D(0, 0, l, 4, 4, 1) mipmapLevel:0 slice:0
                   withBytes:px bytesPerRow:16 bytesPerImage:64];
        else
            [t replaceRegion:MTLRegionMake2D(0, 0, 4, 4) mipmapLevel:0 slice:l
                   withBytes:px bytesPerRow:16 bytesPerImage:64];
    }
}

static MTLRenderPassDescriptor *g3Pass(id<MTLTexture> color, MTLLoadAction la, MTLStoreAction sa)
{
    MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor new];
    rp.colorAttachments[0].texture = color;
    rp.colorAttachments[0].loadAction = la;
    rp.colorAttachments[0].clearColor = MTLClearColorMake(0, 0, 1, 1);
    rp.colorAttachments[0].storeAction = sa;
    return rp;
}

// `keep` collects every allocation (kept alive through the post dump);
// shared non-MSAA 2D textures and the visibility buffer are saved after completion.
static id<MTLCommandBuffer> encodeGles3(id<MTLDevice> dev, id<MTLCommandQueue> q,
                                        id<MTLLibrary> lib, NSString *mode,
                                        NSMutableArray *keep, NSMutableString *info)
{
    const MTLTextureUsage RT = MTLTextureUsageRenderTarget, RTS = RT | MTLTextureUsageShaderRead;
    const MTLPixelFormat BGRA = MTLPixelFormatBGRA8Unorm, NONE = MTLPixelFormatInvalid;
    [info appendFormat:@"scene %@ (gles3)\n", mode];
    id<MTLBuffer> tri = g3Buf(dev, kG3Tri, sizeof(kG3Tri), @"tri positions buffer0 stride16 z=.5", keep, info);
    id<MTLBuffer> full = g3Buf(dev, kG3Full, sizeof(kG3Full), @"full positions buffer0 stride16 z=.75", keep, info);
    id<MTLTexture> rt = g3Tex(dev, MTLTextureType2D, BGRA, 64, 64, 1, 1, MTLStorageModeShared, RTS,
                              @"rt", keep, info);
    if (!rt)
        return nil;
    id<MTLCommandBuffer> cb = [q commandBuffer];
    const G3Fmt *ff = g3Fmt(mode);

    if ([mode isEqualToString:@"g3-base"] || [mode isEqualToString:@"occlusion"]) {
        id<MTLRenderPipelineState> p = g3Pso(dev, lib, "v_pos", "f_const", BGRA, NONE, NONE, 1, info);
        if (!p)
            return nil;
        MTLRenderPassDescriptor *rp = g3Pass(rt, MTLLoadActionClear, MTLStoreActionStore);
        id<MTLBuffer> vis = nil;
        if ([mode isEqualToString:@"occlusion"]) {
            vis = [dev newBufferWithLength:64 options:MTLResourceStorageModeShared];
            memset(vis.contents, 0xa5, 64);
            vis.label = @"vis";
            [keep addObject:vis];
            noteBuffer(info, @"visibility result buffer (sentinel a5, Counting @0, Disabled after)", vis);
            rp.visibilityResultBuffer = vis;
        }
        id<MTLRenderCommandEncoder> re = [cb renderCommandEncoderWithDescriptor:rp];
        [re setRenderPipelineState:p];
        [re setVertexBuffer:tri offset:0 atIndex:0];
        [re setFragmentBytes:kG3Red length:16 atIndex:0];
        if (vis)
            [re setVisibilityResultMode:MTLVisibilityResultModeCounting offset:0];
        [re drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        if (vis)
            [re setVisibilityResultMode:MTLVisibilityResultModeDisabled offset:0];
        observeRenderAllocator(re, mode, @"encoded");
        [re endEncoding];
    } else if ([mode hasPrefix:@"msaa4"]) {
        BOOL resolve = [mode isEqualToString:@"msaa4-resolve"];
        if (![dev supportsTextureSampleCount:4]) {
            [info appendString:@"UNSUPPORTED sampleCount 4\n"];
            return nil;
        }
        id<MTLTexture> ms = g3Tex(dev, MTLTextureType2DMultisample, BGRA, 64, 64, 1, 4,
                                  MTLStorageModePrivate, RT, @"msaa", keep, info);
        id<MTLRenderPipelineState> p = g3Pso(dev, lib, "v_pos", "f_const", BGRA, NONE, NONE, 4, info);
        if (!ms || !p)
            return nil;
        MTLRenderPassDescriptor *rp = g3Pass(ms, MTLLoadActionClear,
                                             resolve ? MTLStoreActionMultisampleResolve : MTLStoreActionStore);
        if (resolve)
            rp.colorAttachments[0].resolveTexture = rt;
        id<MTLRenderCommandEncoder> re = [cb renderCommandEncoderWithDescriptor:rp];
        [re setRenderPipelineState:p];
        [re setVertexBuffer:tri offset:0 atIndex:0];
        [re setFragmentBytes:kG3Red length:16 atIndex:0];
        [re drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        observeRenderAllocator(re, mode, @"encoded");
        [re endEncoding];
    } else if ([mode hasPrefix:@"depth"] || [mode isEqualToString:@"stencil"]) {
        BOOL st = [mode isEqualToString:@"stencil"];
        BOOL second = ![mode isEqualToString:@"depth-store"];
        MTLPixelFormat df = st ? MTLPixelFormatDepth32Float_Stencil8
                          : [mode hasPrefix:@"depth16"] ? MTLPixelFormatDepth16Unorm
                                                        : MTLPixelFormatDepth32Float;
        id<MTLTexture> ds = g3Tex(dev, MTLTextureType2D, df, 64, 64, 1, 1, MTLStorageModePrivate,
                                  RT, @"depth", keep, info);
        id<MTLRenderPipelineState> p = g3Pso(dev, lib, "v_pos", "f_const", BGRA, df, st ? df : NONE, 1, info);
        if (!ds || !p)
            return nil;
        id<MTLDepthStencilState> s1 = st ? g3Dss(dev, NO, MTLCompareFunctionAlways, MTLStencilOperationReplace)
                                         : g3Dss(dev, YES, MTLCompareFunctionAlways, MTLStencilOperationKeep);
        id<MTLDepthStencilState> s2 = st ? g3Dss(dev, NO, MTLCompareFunctionEqual, MTLStencilOperationKeep)
                                         : g3Dss(dev, YES, MTLCompareFunctionAlways, MTLStencilOperationKeep);
        [info appendFormat:@"depth fmt=%lu clear=1.0 pass1 tri z=.5 %s; pass2=%d full z=.75 green %s\n",
                           (unsigned long)df, st ? "stencil Always Replace ref1" : "Less write",
                           second, st ? "stencil Equal ref1" : "Less write"];
        for (int pass = 0; pass < (second ? 2 : 1); pass++) {
            MTLRenderPassDescriptor *rp = g3Pass(rt, pass ? MTLLoadActionLoad : MTLLoadActionClear,
                                                 MTLStoreActionStore);
            rp.depthAttachment.texture = ds;
            rp.depthAttachment.loadAction = pass ? MTLLoadActionLoad : MTLLoadActionClear;
            rp.depthAttachment.clearDepth = 1.0;
            rp.depthAttachment.storeAction = MTLStoreActionStore;
            if (st) {
                rp.stencilAttachment.texture = ds;
                rp.stencilAttachment.loadAction = pass ? MTLLoadActionLoad : MTLLoadActionClear;
                rp.stencilAttachment.clearStencil = 0;
                rp.stencilAttachment.storeAction = MTLStoreActionStore;
            }
            id<MTLRenderCommandEncoder> re = [cb renderCommandEncoderWithDescriptor:rp];
            [re setRenderPipelineState:p];
            [re setDepthStencilState:pass ? s2 : s1];
            if (st)
                [re setStencilReferenceValue:1];
            [re setVertexBuffer:pass ? full : tri offset:0 atIndex:0];
            [re setFragmentBytes:pass ? kG3Green : kG3Red length:16 atIndex:0];
            [re drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
            observeRenderAllocator(re, mode, pass ? @"encoded-pass2" : @"encoded");
            [re endEncoding];
        }
    } else if (ff) {
        id<MTLTexture> ft = g3Tex(dev, MTLTextureType2D, ff->fmt, 64, 64, 1, 1, MTLStorageModeShared,
                                  RTS, @"fmt", keep, info);
        id<MTLRenderPipelineState> p1 = g3Pso(dev, lib, "v_pos", ff->isUint ? "f_const_u" : "f_const",
                                              ff->fmt, NONE, NONE, 1, info);
        id<MTLRenderPipelineState> p2 = g3Pso(dev, lib, "v_pos", ff->isUint ? "f_read_u" : "f_sample",
                                              BGRA, NONE, NONE, 1, info);
        if (!ft || !p1 || !p2)
            return nil;
        static const uint32_t ucol[4] = { 0x12345678u, 0x9au, 0xbcu, 0xffu };
        static const uint32_t ucol8[4] = { 200, 100, 50, 255 };
        static const float fcol[4] = { 0.25f, 0.5f, 0.75f, 1.0f };
        [info appendFormat:@"fmt pass1 clear (0.1,0.2,0.3,1.0) | uint clear (7,8,9,10), tri value %s; pass2 %s full screen into BGRA8 rt\n",
                           ff->isUint ? (ff->bpp == 4 && ff->fmt == MTLPixelFormatR32Uint ? "uint (0x12345678,0x9a,0xbc,0xff)" : "uint (200,100,50,255)")
                                      : "float (0.25,0.5,0.75,1.0)",
                           ff->isUint ? "f_read_u (v&ff)/255" : "f_sample nearest"];
        MTLRenderPassDescriptor *rp1 = g3Pass(ft, MTLLoadActionClear, MTLStoreActionStore);
        rp1.colorAttachments[0].clearColor = ff->isUint ? MTLClearColorMake(7, 8, 9, 10)
                                                        : MTLClearColorMake(0.1, 0.2, 0.3, 1.0);
        id<MTLRenderCommandEncoder> re = [cb renderCommandEncoderWithDescriptor:rp1];
        [re setRenderPipelineState:p1];
        [re setVertexBuffer:tri offset:0 atIndex:0];
        if (ff->isUint)
            [re setFragmentBytes:ff->fmt == MTLPixelFormatR32Uint ? ucol : ucol8 length:16 atIndex:0];
        else
            [re setFragmentBytes:fcol length:16 atIndex:0];
        [re drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        observeRenderAllocator(re, mode, @"encoded");
        [re endEncoding];
        re = [cb renderCommandEncoderWithDescriptor:g3Pass(rt, MTLLoadActionClear, MTLStoreActionStore)];
        [re setRenderPipelineState:p2];
        [re setVertexBuffer:full offset:0 atIndex:0];
        [re setFragmentTexture:ft atIndex:0];
        [re drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        observeRenderAllocator(re, mode, @"encoded-pass2");
        [re endEncoding];
    } else if ([mode hasPrefix:@"tex-"]) {
        BOOL arr = [mode isEqualToString:@"tex-array"], is3D = [mode isEqualToString:@"tex-3d"];
        MTLTextureType tt = arr ? MTLTextureType2DArray : is3D ? MTLTextureType3D : MTLTextureTypeCube;
        NSUInteger layers = arr ? 2 : is3D ? 2 : 6;
        id<MTLTexture> src = g3Tex(dev, tt, MTLPixelFormatRGBA8Unorm, 4, 4, layers, 1,
                                   MTLStorageModeShared, MTLTextureUsageShaderRead, @"src", keep, info);
        const char *fs = arr ? "f_array" : is3D ? "f_3d" : "f_cube";
        id<MTLRenderPipelineState> p = g3Pso(dev, lib, "v_pos", fs, BGRA, NONE, NONE, 1, info);
        if (!src || !p)
            return nil;
        g3Fill(src, layers, is3D);
        [info appendFormat:@"src 4x4 RGBA8 x%lu, texel x*60|y*60<<8|L*40<<16|ff<<24, sampled %s nearest\n",
                           (unsigned long)layers, arr ? "layer 1" : is3D ? "r=0.75 (slice 1)" : "face 3 (-Y)"];
        id<MTLRenderCommandEncoder> re =
            [cb renderCommandEncoderWithDescriptor:g3Pass(rt, MTLLoadActionClear, MTLStoreActionStore)];
        [re setRenderPipelineState:p];
        [re setVertexBuffer:full offset:0 atIndex:0];
        [re setFragmentTexture:src atIndex:0];
        [re drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        observeRenderAllocator(re, mode, @"encoded");
        [re endEncoding];
    } else if ([mode isEqualToString:@"instanced"]) {
        static const float off[4][2] = { { -.5f, -.5f }, { .5f, -.5f }, { -.5f, .5f }, { .5f, .5f } };
        id<MTLBuffer> ob = g3Buf(dev, off, sizeof(off), @"instance offsets buffer1 float2", keep, info);
        id<MTLRenderPipelineState> p = g3Pso(dev, lib, "v_inst", "f_inst", BGRA, NONE, NONE, 1, info);
        if (!p)
            return nil;
        id<MTLRenderCommandEncoder> re =
            [cb renderCommandEncoderWithDescriptor:g3Pass(rt, MTLLoadActionClear, MTLStoreActionStore)];
        [re setRenderPipelineState:p];
        [re setVertexBuffer:tri offset:0 atIndex:0];
        [re setVertexBuffer:ob offset:0 atIndex:1];
        [re drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3 instanceCount:4];
        observeRenderAllocator(re, mode, @"encoded");
        [re endEncoding];
    } else {
        return nil;
    }
    return cb;
}

static unsigned g3Bpp(MTLPixelFormat f)
{
    if (f == MTLPixelFormatBGRA8Unorm)
        return 4;
    for (size_t i = 0; i < sizeof(kG3Fmts) / sizeof(kG3Fmts[0]); i++)
        if (kG3Fmts[i].fmt == f)
            return kG3Fmts[i].bpp;
    return 0;
}

static void saveGles3(NSString *mode, NSArray *keep, NSMutableString *idx)
{
    for (id o in keep) {
        if ([o conformsToProtocol:@protocol(MTLTexture)]) {
            id<MTLTexture> t = o;
            unsigned bpp = g3Bpp(t.pixelFormat);
            if (t.textureType != MTLTextureType2D || t.storageMode != MTLStorageModeShared || !bpp ||
                t.width != 64)
                continue;
            NSMutableData *px = [NSMutableData dataWithLength:64 * 64 * bpp];
            [t getBytes:px.mutableBytes bytesPerRow:64 * bpp fromRegion:MTLRegionMake2D(0, 0, 64, 64)
             mipmapLevel:0];
            writeData([NSString stringWithFormat:@"g3-%@-%@-after.raw", mode, t.label], px);
            const uint8_t *b = px.bytes;
            NSMutableString *s = [NSMutableString string];
            static const unsigned pts[4][2] = { { 0, 0 }, { 32, 40 }, { 16, 16 }, { 63, 63 } };
            for (int i = 0; i < 4; i++) {
                [s appendFormat:@" px(%u,%u)=", pts[i][0], pts[i][1]];
                for (unsigned k = 0; k < bpp; k++)
                    [s appendFormat:@"%02x", b[(pts[i][1] * 64 + pts[i][0]) * bpp + k]];
            }
            [idx appendFormat:@"# %@ fmt=%lu bpp=%u bytes:%@\n", t.label, (unsigned long)t.pixelFormat,
                              bpp, s];
        } else if ([o conformsToProtocol:@protocol(MTLBuffer)] && [[(id<MTLBuffer>)o label] isEqualToString:@"vis"]) {
            id<MTLBuffer> v = o;
            writeData([NSString stringWithFormat:@"g3-%@-vis-after.raw", mode],
                      [NSData dataWithBytes:v.contents length:v.length]);
            [idx appendFormat:@"# vis[0..7] after: %@\n", hexWords(v.contents, 8)];
        }
    }
}
