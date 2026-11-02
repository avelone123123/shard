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
end