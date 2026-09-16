#pragma once

// Scalar mul_mm indexes its logical lanes with gl_LocalInvocationID, not
// subgroup intrinsics. A hardware subgroup wider than the tile is not a valid
// logical warp: e.g. BM=64, WM=128 makes BM/WM zero in the shader. Cooperative
// matrix and <=64-lane configurations keep their existing behavior.
constexpr unsigned aii_scalar_tile_warp(unsigned hardware, bool cooperative) {
    return !cooperative && hardware > 64 ? 32 : (hardware < 8 ? 8 : hardware);
}
