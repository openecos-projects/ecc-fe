`timescale 1ns/1ps

module ysyx_00000000_axi #(
  parameter bit LEGACY_RTC = 1'b0
) (
  input  logic        clock,
  input  logic        reset,

  input  logic        if_req_valid,
  output logic        if_req_ready,
  input  logic [31:0] if_req_addr,
  output logic        if_resp_valid,
  input  logic        if_resp_ready,
  output logic [31:0] if_resp_data,
  output logic        if_resp_error,

  input  logic        d_req_valid,
  output logic        d_req_ready,
  input  logic        d_req_write,
  input  logic [31:0] d_req_addr,
  input  logic [1:0]  d_req_size,
  input  logic [31:0] d_req_wdata,
  input  logic [3:0]  d_req_wstrb,
  output logic        d_resp_valid,
  input  logic        d_resp_ready,
  output logic [31:0] d_resp_rdata,
  output logic        d_resp_error,

  input  logic        io_master_awready,
  output logic        io_master_awvalid,
  output logic [31:0] io_master_awaddr,
  output logic [3:0]  io_master_awid,
  output logic [7:0]  io_master_awlen,
  output logic [2:0]  io_master_awsize,
  output logic [1:0]  io_master_awburst,
  input  logic        io_master_wready,
  output logic        io_master_wvalid,
  output logic [31:0] io_master_wdata,
  output logic [3:0]  io_master_wstrb,
  output logic        io_master_wlast,
  output logic        io_master_bready,
  input  logic        io_master_bvalid,
  input  logic [1:0]  io_master_bresp,
  input  logic [3:0]  io_master_bid,
  input  logic        io_master_arready,
  output logic        io_master_arvalid,
  output logic [31:0] io_master_araddr,
  output logic [3:0]  io_master_arid,
  output logic [7:0]  io_master_arlen,
  output logic [2:0]  io_master_arsize,
  output logic [1:0]  io_master_arburst,
  output logic        io_master_rready,
  input  logic        io_master_rvalid,
  input  logic [1:0]  io_master_rresp,
  input  logic [31:0] io_master_rdata,
  input  logic        io_master_rlast,
  input  logic [3:0]  io_master_rid
);

  localparam logic [2:0] STATE_IDLE       = 3'd0;
  localparam logic [2:0] STATE_READ_ADDR  = 3'd1;
  localparam logic [2:0] STATE_READ_DATA  = 3'd2;
  localparam logic [2:0] STATE_WRITE_DATA = 3'd3;
  localparam logic [2:0] STATE_WRITE_RESP = 3'd4;
  localparam logic [2:0] STATE_LOCAL_RESP = 3'd5;

  logic [2:0] state;
  logic       owner_data;
  logic [31:0] request_addr;
  logic [1:0] request_size;
  logic [31:0] request_wdata;
  logic [3:0] request_wstrb;
  logic       aw_done;
  logic       w_done;
  logic [31:0] local_response_data;
  logic [63:0] rtc_counter;

  logic aw_fire;
  logic w_fire;
  logic b_fire;
  logic ar_fire;
  logic r_fire;
  logic local_rtc_request;

  assign local_rtc_request = LEGACY_RTC && !d_req_write &&
                             ((d_req_addr == 32'ha000_0048) ||
                              (d_req_addr == 32'ha000_004c));

  always_comb begin
    if_req_ready = 1'b0;
    d_req_ready = 1'b0;
    if_resp_valid = 1'b0;
    if_resp_data = 32'b0;
    if_resp_error = 1'b0;
    d_resp_valid = 1'b0;
    d_resp_rdata = 32'b0;
    d_resp_error = 1'b0;

    io_master_awvalid = 1'b0;
    io_master_awaddr = request_addr;
    io_master_awid = 4'b0;
    io_master_awlen = 8'b0;
    io_master_awsize = {1'b0, request_size};
    io_master_awburst = 2'b01;
    io_master_wvalid = 1'b0;
    io_master_wdata = request_wdata;
    io_master_wstrb = request_wstrb;
    io_master_wlast = 1'b1;
    io_master_bready = 1'b0;
    io_master_arvalid = 1'b0;
    io_master_araddr = request_addr;
    io_master_arid = 4'b0;
    io_master_arlen = 8'b0;
    io_master_arsize = {1'b0, request_size};
    io_master_arburst = 2'b01;
    io_master_rready = 1'b0;

    unique case (state)
      STATE_IDLE: begin
        d_req_ready = 1'b1;
        if_req_ready = !d_req_valid;
      end
      STATE_READ_ADDR: begin
        io_master_arvalid = 1'b1;
      end
      STATE_READ_DATA: begin
        if (owner_data) begin
          d_resp_valid = io_master_rvalid;
          d_resp_rdata = io_master_rdata;
          d_resp_error = (io_master_rresp != 2'b00) || !io_master_rlast ||
                         (io_master_rid != 4'b0);
          io_master_rready = d_resp_ready;
        end else begin
          if_resp_valid = io_master_rvalid;
          if_resp_data = io_master_rdata;
          if_resp_error = (io_master_rresp != 2'b00) || !io_master_rlast ||
                          (io_master_rid != 4'b0);
          io_master_rready = if_resp_ready;
        end
      end
      STATE_WRITE_DATA: begin
        io_master_awvalid = !aw_done;
        io_master_wvalid = !w_done;
      end
      STATE_WRITE_RESP: begin
        d_resp_valid = io_master_bvalid;
        d_resp_error = (io_master_bresp != 2'b00) || (io_master_bid != 4'b0);
        io_master_bready = d_resp_ready;
      end
      STATE_LOCAL_RESP: begin
        d_resp_valid = 1'b1;
        d_resp_rdata = local_response_data;
      end
      default: ;
    endcase
  end

  assign aw_fire = io_master_awvalid && io_master_awready;
  assign w_fire = io_master_wvalid && io_master_wready;
  assign b_fire = io_master_bvalid && io_master_bready;
  assign ar_fire = io_master_arvalid && io_master_arready;
  assign r_fire = io_master_rvalid && io_master_rready;

  always_ff @(posedge clock) begin
    if (reset) begin
      state               <= STATE_IDLE;
      owner_data          <= 1'b0;
      request_addr        <= 32'b0;
      request_size        <= 2'b0;
      request_wdata       <= 32'b0;
      request_wstrb       <= 4'b0;
      aw_done             <= 1'b0;
      w_done              <= 1'b0;
      local_response_data <= 32'b0;
      rtc_counter         <= 64'b0;
    end else begin
      rtc_counter <= rtc_counter + 64'd1;

      unique case (state)
        STATE_IDLE: begin
          aw_done <= 1'b0;
          w_done <= 1'b0;
          if (d_req_valid && d_req_ready) begin
            owner_data    <= 1'b1;
            request_addr  <= d_req_addr;
            request_size  <= d_req_size;
            request_wdata <= d_req_wdata;
            request_wstrb <= d_req_wstrb;
            if (local_rtc_request) begin
              if (d_req_addr == 32'ha000_0048) begin
                local_response_data <= rtc_counter[31:0];
              end else begin
                local_response_data <= rtc_counter[63:32];
              end
              state <= STATE_LOCAL_RESP;
            end else if (d_req_write) begin
              state <= STATE_WRITE_DATA;
            end else begin
              state <= STATE_READ_ADDR;
            end
          end else if (if_req_valid && if_req_ready) begin
            owner_data   <= 1'b0;
            request_addr <= if_req_addr;
            request_size <= 2'd2;
            state        <= STATE_READ_ADDR;
          end
        end
        STATE_READ_ADDR: begin
          if (ar_fire) state <= STATE_READ_DATA;
        end
        STATE_READ_DATA: begin
          if (r_fire) state <= STATE_IDLE;
        end
        STATE_WRITE_DATA: begin
          if (aw_fire) aw_done <= 1'b1;
          if (w_fire) w_done <= 1'b1;
          if ((aw_done || aw_fire) && (w_done || w_fire)) state <= STATE_WRITE_RESP;
        end
        STATE_WRITE_RESP: begin
          if (b_fire) state <= STATE_IDLE;
        end
        STATE_LOCAL_RESP: begin
          if (d_resp_valid && d_resp_ready) state <= STATE_IDLE;
        end
        default: state <= STATE_IDLE;
      endcase
    end
  end

endmodule
