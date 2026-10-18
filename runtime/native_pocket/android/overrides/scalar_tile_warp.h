#pragma once

constexpr unsigned aii_scalar_tile_warp(unsigned hardware, bool cooperative) {
    return !cooperative && hardware > 64 ? 32 : (hardware < 8 ? 8 : hardware);
}
