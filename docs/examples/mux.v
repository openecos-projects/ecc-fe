// Simple 2-to-1 multiplexer (8-bit)
module mux (
    input  [7:0] a,
    input  [7:0] b,
    input        sel,
    output [7:0] y
);
    assign y = sel ? b : a;
endmodule
