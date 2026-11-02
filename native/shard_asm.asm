.code
shard_asm_dot_f32 proc
    vxorps ymm0, ymm0, ymm0
    mov eax, r8d
    shr eax, 3
    test eax, eax
    jz scalar_tail
loop_avx2:
    vmovups ymm2, ymmword ptr [rcx]
    vmovups ymm3, ymmword ptr [rdx]
    vfmadd231ps ymm0, ymm2, ymm3
    add rcx, 32
    add rdx, 32
    dec eax
    jnz loop_avx2
    vextractf128 xmm2, ymm0, 1
    vaddps xmm0, xmm0, xmm2
    vhaddps xmm0, xmm0, xmm0
    vhaddps xmm0, xmm0, xmm0
scalar_tail:
    and r8d, 7
    jz done
loop_scalar:
    vmovss xmm2, real4 ptr [rcx]
    vmovss xmm3, real4 ptr [rdx]
    vfmadd231ss xmm0, xmm2, xmm3
    add rcx, 4
    add rdx, 4
    dec r8d
    jnz loop_scalar
done:
    vzeroupper
    ret
shard_asm_dot_f32 endp
shard_asm_dot_unroll8 proc
    vxorps ymm4, ymm4, ymm4
    vxorps ymm5, ymm5, ymm5
    mov eax, r8d
    shr eax, 5
    test eax, eax
    jz unroll_tail
loop_unroll:
    vmovups ymm0, ymmword ptr [rcx]
    vmovups ymm1, ymmword ptr [rcx+32]
    vmovups ymm2, ymmword ptr [rcx+64]
    vmovups ymm3, ymmword ptr [rcx+96]
    vfmadd231ps ymm4, ymm0, ymmword ptr [rdx]
    vfmadd231ps ymm5, ymm1, ymmword ptr [rdx+32]
    vfmadd231ps ymm4, ymm2, ymmword ptr [rdx+64]
    vfmadd231ps ymm5, ymm3, ymmword ptr [rdx+96]
    add rcx, 128
    add rdx, 128
    dec eax
    jnz loop_unroll
    vaddps ymm4, ymm4, ymm5
    vextractf128 xmm2, ymm4, 1
    vaddps xmm0, xmm4, xmm2
    vhaddps xmm0, xmm0, xmm0
    vhaddps xmm0, xmm0, xmm0
unroll_tail:
    and r8d, 31
    jz unroll_done
loop_unroll_scalar:
    vmovss xmm2, real4 ptr [rcx]
    vmovss xmm3, real4 ptr [rdx]
    vfmadd231ss xmm0, xmm2, xmm3
    add rcx, 4
    add rdx, 4
    dec r8d
    jnz loop_unroll_scalar
unroll_done:
    vzeroupper
    ret
shard_asm_dot_unroll8 endp
shard_asm_fast_scale proc
    vmovss xmm1, real4 ptr [r8]
    vbroadcastss ymm1, xmm1
    mov eax, r9d
    shr eax, 3
    test eax, eax
    jz scale_scalar
scale_loop:
    vmovups ymm0, ymmword ptr [rcx]
    vmulps ymm0, ymm0, ymm1
    vmovups ymmword ptr [rdx], ymm0
    add rcx, 32
    add rdx, 32
    dec eax
    jnz scale_loop
scale_scalar:
    and r9d, 7
    jz scale_done
scale_tail:
    vmovss xmm0, real4 ptr [rcx]
    vmulss xmm0, xmm0, xmm1
    vmovss real4 ptr [rdx], xmm0
    add rcx, 4
    add rdx, 4
    dec r9d
    jnz scale_tail
scale_done:
    vzeroupper
    ret
shard_asm_fast_scale endp
shard_asm_rmsnorm_sum_sq proc
    vxorps ymm0, ymm0, ymm0
    mov eax, edx
    shr eax, 3
    test eax, eax
    jz rms_scalar
rms_loop:
    vmovups ymm2, ymmword ptr [rcx]
    vfmadd231ps ymm0, ymm2, ymm2
    add rcx, 32
    dec eax
    jnz rms_loop
    vextractf128 xmm2, ymm0, 1
    vaddps xmm0, xmm0, xmm2
    vhaddps xmm0, xmm0, xmm0
    vhaddps xmm0, xmm0, xmm0
rms_scalar:
    and edx, 7
    jz rms_done
rms_tail:
    vmovss xmm2, real4 ptr [rcx]
    vfmadd231ss xmm0, xmm2, xmm2
    add rcx, 4
    dec edx
    jnz rms_tail
rms_done:
    vzeroupper
    ret
shard_asm_rmsnorm_sum_sq endp
shard_asm_vec_add_f32 proc
    mov eax, r9d
    shr eax, 3
    test eax, eax
    jz add_scalar
add_loop:
    vmovups ymm0, ymmword ptr [rcx]
    vmovups ymm1, ymmword ptr [rdx]
    vaddps ymm0, ymm0, ymm1
    vmovups ymmword ptr [r8], ymm0
    add rcx, 32
    add rdx, 32
    add r8, 32
    dec eax
    jnz add_loop
add_scalar:
    and r9d, 7
    jz add_done
add_tail:
    vmovss xmm0, real4 ptr [rcx]
    vaddss xmm0, xmm0, real4 ptr [rdx]
    vmovss real4 ptr [r8], xmm0
    add rcx, 4
    add rdx, 4
    add r8, 4
    dec r9d
    jnz add_tail
add_done:
    vzeroupper
    ret
shard_asm_vec_add_f32 endp
end