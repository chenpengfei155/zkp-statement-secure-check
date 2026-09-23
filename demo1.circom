pragma circom 2.0.0;

template ArrayXOR(n) {
    signal input a[n];
    signal input b[n];
    signal output out[n];
    
    for (var i = 0; i < n; i++) {
        // Each input element must be a bit: 0 or 1.
        a[i] * (a[i] - 1) === 0;
        b[i] * (b[i] - 1) === 0;

        // XOR for bits, with the result constrained to this expression.
        out[i] <== a[i] + b[i] - 2 * a[i] * b[i];
    }
}

component main = ArrayXOR(2);
