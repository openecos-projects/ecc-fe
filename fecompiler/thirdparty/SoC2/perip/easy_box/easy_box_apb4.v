module easy_box_apb4_base (
  input         apb4_pclk,
  input         apb4_presetn,
  input         apb4_psel,
  input         apb4_penable,
  input         apb4_pwrite,
  input  [31:0] apb4_paddr,
  input  [2:0]  apb4_pprot,
  input  [31:0] apb4_pwdata,
  input  [3:0]  apb4_pstrb,
  output        apb4_pready,
  output        apb4_pslverr,
  output [31:0] apb4_prdata
);
  assign apb4_pready = 1'b1;
  assign apb4_pslverr = 1'b0;
  assign apb4_prdata = 32'h0;
endmodule

module apb4_archinfo (
  input         apb4_pclk,
  input         apb4_presetn,
  input         apb4_psel,
  input         apb4_penable,
  input         apb4_pwrite,
  input  [31:0] apb4_paddr,
  input  [2:0]  apb4_pprot,
  input  [31:0] apb4_pwdata,
  input  [3:0]  apb4_pstrb,
  output        apb4_pready,
  output        apb4_pslverr,
  output [31:0] apb4_prdata
);
  easy_box_apb4_base u_base (
    .apb4_pclk(apb4_pclk),
    .apb4_presetn(apb4_presetn),
    .apb4_psel(apb4_psel),
    .apb4_penable(apb4_penable),
    .apb4_pwrite(apb4_pwrite),
    .apb4_paddr(apb4_paddr),
    .apb4_pprot(apb4_pprot),
    .apb4_pwdata(apb4_pwdata),
    .apb4_pstrb(apb4_pstrb),
    .apb4_pready(apb4_pready),
    .apb4_pslverr(apb4_pslverr),
    .apb4_prdata(apb4_prdata)
  );
endmodule

module apb4_clint (
  input         apb4_pclk,
  input         apb4_presetn,
  input         apb4_psel,
  input         apb4_penable,
  input         apb4_pwrite,
  input  [31:0] apb4_paddr,
  input  [2:0]  apb4_pprot,
  input  [31:0] apb4_pwdata,
  input  [3:0]  apb4_pstrb,
  output        apb4_pready,
  output        apb4_pslverr,
  output [31:0] apb4_prdata,
  output        clint_tmr_irq_o,
  output        clint_sfr_irq_o
);
  easy_box_apb4_base u_base (
    .apb4_pclk(apb4_pclk),
    .apb4_presetn(apb4_presetn),
    .apb4_psel(apb4_psel),
    .apb4_penable(apb4_penable),
    .apb4_pwrite(apb4_pwrite),
    .apb4_paddr(apb4_paddr),
    .apb4_pprot(apb4_pprot),
    .apb4_pwdata(apb4_pwdata),
    .apb4_pstrb(apb4_pstrb),
    .apb4_pready(apb4_pready),
    .apb4_pslverr(apb4_pslverr),
    .apb4_prdata(apb4_prdata)
  );
  assign clint_tmr_irq_o = 1'b0;
  assign clint_sfr_irq_o = 1'b0;
endmodule

module apb4_crc (
  input         apb4_pclk,
  input         apb4_presetn,
  input         apb4_psel,
  input         apb4_penable,
  input         apb4_pwrite,
  input  [31:0] apb4_paddr,
  input  [2:0]  apb4_pprot,
  input  [31:0] apb4_pwdata,
  input  [3:0]  apb4_pstrb,
  output        apb4_pready,
  output        apb4_pslverr,
  output [31:0] apb4_prdata
);
  easy_box_apb4_base u_base (
    .apb4_pclk(apb4_pclk),
    .apb4_presetn(apb4_presetn),
    .apb4_psel(apb4_psel),
    .apb4_penable(apb4_penable),
    .apb4_pwrite(apb4_pwrite),
    .apb4_paddr(apb4_paddr),
    .apb4_pprot(apb4_pprot),
    .apb4_pwdata(apb4_pwdata),
    .apb4_pstrb(apb4_pstrb),
    .apb4_pready(apb4_pready),
    .apb4_pslverr(apb4_pslverr),
    .apb4_prdata(apb4_prdata)
  );
endmodule

module apb4_gpio (
  input         apb4_pclk,
  input         apb4_presetn,
  input         apb4_psel,
  input         apb4_penable,
  input         apb4_pwrite,
  input  [31:0] apb4_paddr,
  input  [2:0]  apb4_pprot,
  input  [31:0] apb4_pwdata,
  input  [3:0]  apb4_pstrb,
  output        apb4_pready,
  output        apb4_pslverr,
  output [31:0] apb4_prdata,
  input  [31:0] gpio_gpio_in_i,
  output [31:0] gpio_gpio_out_o,
  output [31:0] gpio_gpio_dir_o,
  output [31:0] gpio_gpio_alt_in_o,
  input  [31:0] gpio_gpio_alt_0_out_i,
  input  [31:0] gpio_gpio_alt_0_dir_i,
  input  [31:0] gpio_gpio_alt_1_out_i,
  input  [31:0] gpio_gpio_alt_1_dir_i,
  output        gpio_irq_o
);
  easy_box_apb4_base u_base (
    .apb4_pclk(apb4_pclk),
    .apb4_presetn(apb4_presetn),
    .apb4_psel(apb4_psel),
    .apb4_penable(apb4_penable),
    .apb4_pwrite(apb4_pwrite),
    .apb4_paddr(apb4_paddr),
    .apb4_pprot(apb4_pprot),
    .apb4_pwdata(apb4_pwdata),
    .apb4_pstrb(apb4_pstrb),
    .apb4_pready(apb4_pready),
    .apb4_pslverr(apb4_pslverr),
    .apb4_prdata(apb4_prdata)
  );
  assign gpio_gpio_out_o = 32'h0;
  assign gpio_gpio_dir_o = 32'h0;
  assign gpio_gpio_alt_in_o = 32'h0;
  assign gpio_irq_o = 1'b0;
endmodule

module apb4_i2c (
  input         apb4_pclk,
  input         apb4_presetn,
  input         apb4_psel,
  input         apb4_penable,
  input         apb4_pwrite,
  input  [31:0] apb4_paddr,
  input  [2:0]  apb4_pprot,
  input  [31:0] apb4_pwdata,
  input  [3:0]  apb4_pstrb,
  output        apb4_pready,
  output        apb4_pslverr,
  output [31:0] apb4_prdata,
  input         i2c_scl_i,
  output        i2c_scl_o,
  output        i2c_scl_dir_o,
  input         i2c_sda_i,
  output        i2c_sda_o,
  output        i2c_sda_dir_o,
  output        i2c_irq_o
);
  easy_box_apb4_base u_base (
    .apb4_pclk(apb4_pclk),
    .apb4_presetn(apb4_presetn),
    .apb4_psel(apb4_psel),
    .apb4_penable(apb4_penable),
    .apb4_pwrite(apb4_pwrite),
    .apb4_paddr(apb4_paddr),
    .apb4_pprot(apb4_pprot),
    .apb4_pwdata(apb4_pwdata),
    .apb4_pstrb(apb4_pstrb),
    .apb4_pready(apb4_pready),
    .apb4_pslverr(apb4_pslverr),
    .apb4_prdata(apb4_prdata)
  );
  assign i2c_scl_o = 1'b1;
  assign i2c_scl_dir_o = 1'b0;
  assign i2c_sda_o = 1'b1;
  assign i2c_sda_dir_o = 1'b0;
  assign i2c_irq_o = 1'b0;
endmodule

module apb4_i2s (
  input         apb4_pclk,
  input         apb4_presetn,
  input         apb4_psel,
  input         apb4_penable,
  input         apb4_pwrite,
  input  [31:0] apb4_paddr,
  input  [2:0]  apb4_pprot,
  input  [31:0] apb4_pwdata,
  input  [3:0]  apb4_pstrb,
  output        apb4_pready,
  output        apb4_pslverr,
  output [31:0] apb4_prdata,
  output        i2s_mclk_o,
  output        i2s_sck_o,
  input         i2s_sck_i,
  output        i2s_sck_en_o,
  output        i2s_ws_o,
  input         i2s_ws_i,
  output        i2s_ws_en_o,
  output        i2s_sd_o,
  input         i2s_sd_i,
  output        i2s_irq_o
);
  easy_box_apb4_base u_base (
    .apb4_pclk(apb4_pclk),
    .apb4_presetn(apb4_presetn),
    .apb4_psel(apb4_psel),
    .apb4_penable(apb4_penable),
    .apb4_pwrite(apb4_pwrite),
    .apb4_paddr(apb4_paddr),
    .apb4_pprot(apb4_pprot),
    .apb4_pwdata(apb4_pwdata),
    .apb4_pstrb(apb4_pstrb),
    .apb4_pready(apb4_pready),
    .apb4_pslverr(apb4_pslverr),
    .apb4_prdata(apb4_prdata)
  );
  assign i2s_mclk_o = 1'b0;
  assign i2s_sck_o = 1'b0;
  assign i2s_sck_en_o = 1'b0;
  assign i2s_ws_o = 1'b0;
  assign i2s_ws_en_o = 1'b0;
  assign i2s_sd_o = 1'b0;
  assign i2s_irq_o = 1'b0;
endmodule

module apb4_plic (
  input         apb4_pclk,
  input         apb4_presetn,
  input         apb4_psel,
  input         apb4_penable,
  input         apb4_pwrite,
  input  [31:0] apb4_paddr,
  input  [2:0]  apb4_pprot,
  input  [31:0] apb4_pwdata,
  input  [3:0]  apb4_pstrb,
  output        apb4_pready,
  output        apb4_pslverr,
  output [31:0] apb4_prdata,
  input  [31:0] plic_irq_i,
  output        plic_irq_o
);
  easy_box_apb4_base u_base (
    .apb4_pclk(apb4_pclk),
    .apb4_presetn(apb4_presetn),
    .apb4_psel(apb4_psel),
    .apb4_penable(apb4_penable),
    .apb4_pwrite(apb4_pwrite),
    .apb4_paddr(apb4_paddr),
    .apb4_pprot(apb4_pprot),
    .apb4_pwdata(apb4_pwdata),
    .apb4_pstrb(apb4_pstrb),
    .apb4_pready(apb4_pready),
    .apb4_pslverr(apb4_pslverr),
    .apb4_prdata(apb4_prdata)
  );
  assign plic_irq_o = 1'b0;
endmodule

module apb4_ps2 (
  input         apb4_pclk,
  input         apb4_presetn,
  input         apb4_psel,
  input         apb4_penable,
  input         apb4_pwrite,
  input  [31:0] apb4_paddr,
  input  [2:0]  apb4_pprot,
  input  [31:0] apb4_pwdata,
  input  [3:0]  apb4_pstrb,
  output        apb4_pready,
  output        apb4_pslverr,
  output [31:0] apb4_prdata,
  input         ps2_ps2_clk_i,
  input         ps2_ps2_dat_i,
  output        ps2_irq_o
);
  easy_box_apb4_base u_base (
    .apb4_pclk(apb4_pclk),
    .apb4_presetn(apb4_presetn),
    .apb4_psel(apb4_psel),
    .apb4_penable(apb4_penable),
    .apb4_pwrite(apb4_pwrite),
    .apb4_paddr(apb4_paddr),
    .apb4_pprot(apb4_pprot),
    .apb4_pwdata(apb4_pwdata),
    .apb4_pstrb(apb4_pstrb),
    .apb4_pready(apb4_pready),
    .apb4_pslverr(apb4_pslverr),
    .apb4_prdata(apb4_prdata)
  );
  assign ps2_irq_o = 1'b0;
endmodule

module apb4_pwm (
  input         apb4_pclk,
  input         apb4_presetn,
  input         apb4_psel,
  input         apb4_penable,
  input         apb4_pwrite,
  input  [31:0] apb4_paddr,
  input  [2:0]  apb4_pprot,
  input  [31:0] apb4_pwdata,
  input  [3:0]  apb4_pstrb,
  output        apb4_pready,
  output        apb4_pslverr,
  output [31:0] apb4_prdata,
  output [3:0]  pwm_pwm_o,
  output        pwm_irq_o
);
  easy_box_apb4_base u_base (
    .apb4_pclk(apb4_pclk),
    .apb4_presetn(apb4_presetn),
    .apb4_psel(apb4_psel),
    .apb4_penable(apb4_penable),
    .apb4_pwrite(apb4_pwrite),
    .apb4_paddr(apb4_paddr),
    .apb4_pprot(apb4_pprot),
    .apb4_pwdata(apb4_pwdata),
    .apb4_pstrb(apb4_pstrb),
    .apb4_pready(apb4_pready),
    .apb4_pslverr(apb4_pslverr),
    .apb4_prdata(apb4_prdata)
  );
  assign pwm_pwm_o = 4'h0;
  assign pwm_irq_o = 1'b0;
endmodule

module apb4_rcu (
  input         apb4_pclk,
  input         apb4_presetn,
  input         apb4_psel,
  input         apb4_penable,
  input         apb4_pwrite,
  input  [31:0] apb4_paddr,
  input  [2:0]  apb4_pprot,
  input  [31:0] apb4_pwdata,
  input  [3:0]  apb4_pstrb,
  output        apb4_pready,
  output        apb4_pslverr,
  output [31:0] apb4_prdata,
  input         rcu_ext_lfosc_clk_i,
  input         rcu_ext_hfosc_clk_i,
  input         rcu_ext_audosc_clk_i,
  input         rcu_ext_rst_n_i,
  input         rcu_wdt_rst_n_i,
  input         rcu_pll_en_i,
  input  [2:0]  rcu_clk_cfg_i,
  input  [4:0]  rcu_core_sel_i,
  output [4:0]  rcu_core_sel_o,
  output [6:0]  rcu_clk_o,
  output [6:0]  rcu_rst_n_o
);
  easy_box_apb4_base u_base (
    .apb4_pclk(apb4_pclk),
    .apb4_presetn(apb4_presetn),
    .apb4_psel(apb4_psel),
    .apb4_penable(apb4_penable),
    .apb4_pwrite(apb4_pwrite),
    .apb4_paddr(apb4_paddr),
    .apb4_pprot(apb4_pprot),
    .apb4_pwdata(apb4_pwdata),
    .apb4_pstrb(apb4_pstrb),
    .apb4_pready(apb4_pready),
    .apb4_pslverr(apb4_pslverr),
    .apb4_prdata(apb4_prdata)
  );
  assign rcu_core_sel_o = 5'h0;
  assign rcu_clk_o = 7'h0;
  assign rcu_rst_n_o = 7'h7f;
endmodule

module apb4_rng (
  input         apb4_pclk,
  input         apb4_presetn,
  input         apb4_psel,
  input         apb4_penable,
  input         apb4_pwrite,
  input  [31:0] apb4_paddr,
  input  [2:0]  apb4_pprot,
  input  [31:0] apb4_pwdata,
  input  [3:0]  apb4_pstrb,
  output        apb4_pready,
  output        apb4_pslverr,
  output [31:0] apb4_prdata
);
  easy_box_apb4_base u_base (
    .apb4_pclk(apb4_pclk),
    .apb4_presetn(apb4_presetn),
    .apb4_psel(apb4_psel),
    .apb4_penable(apb4_penable),
    .apb4_pwrite(apb4_pwrite),
    .apb4_paddr(apb4_paddr),
    .apb4_pprot(apb4_pprot),
    .apb4_pwdata(apb4_pwdata),
    .apb4_pstrb(apb4_pstrb),
    .apb4_pready(apb4_pready),
    .apb4_pslverr(apb4_pslverr),
    .apb4_prdata(apb4_prdata)
  );
endmodule

module apb4_rtc (
  input         apb4_pclk,
  input         apb4_presetn,
  input         apb4_psel,
  input         apb4_penable,
  input         apb4_pwrite,
  input  [31:0] apb4_paddr,
  input  [2:0]  apb4_pprot,
  input  [31:0] apb4_pwdata,
  input  [3:0]  apb4_pstrb,
  output        apb4_pready,
  output        apb4_pslverr,
  output [31:0] apb4_prdata,
  input         rtc_rtc_clk_i,
  input         rtc_rtc_rst_n_i,
  output        rtc_irq_o
);
  easy_box_apb4_base u_base (
    .apb4_pclk(apb4_pclk),
    .apb4_presetn(apb4_presetn),
    .apb4_psel(apb4_psel),
    .apb4_penable(apb4_penable),
    .apb4_pwrite(apb4_pwrite),
    .apb4_paddr(apb4_paddr),
    .apb4_pprot(apb4_pprot),
    .apb4_pwdata(apb4_pwdata),
    .apb4_pstrb(apb4_pstrb),
    .apb4_pready(apb4_pready),
    .apb4_pslverr(apb4_pslverr),
    .apb4_prdata(apb4_prdata)
  );
  assign rtc_irq_o = 1'b0;
endmodule

module apb4_spi (
  input         apb4_pclk,
  input         apb4_presetn,
  input         apb4_psel,
  input         apb4_penable,
  input         apb4_pwrite,
  input  [31:0] apb4_paddr,
  input  [2:0]  apb4_pprot,
  input  [31:0] apb4_pwdata,
  input  [3:0]  apb4_pstrb,
  output        apb4_pready,
  output        apb4_pslverr,
  output [31:0] apb4_prdata,
  output        qspi_spi_sck_o,
  output [3:0]  qspi_spi_nss_o,
  output [3:0]  qspi_spi_io_en_o,
  input  [3:0]  qspi_spi_io_in_i,
  output [3:0]  qspi_spi_io_out_o,
  output        qspi_irq_o
);
  easy_box_apb4_base u_base (
    .apb4_pclk(apb4_pclk),
    .apb4_presetn(apb4_presetn),
    .apb4_psel(apb4_psel),
    .apb4_penable(apb4_penable),
    .apb4_pwrite(apb4_pwrite),
    .apb4_paddr(apb4_paddr),
    .apb4_pprot(apb4_pprot),
    .apb4_pwdata(apb4_pwdata),
    .apb4_pstrb(apb4_pstrb),
    .apb4_pready(apb4_pready),
    .apb4_pslverr(apb4_pslverr),
    .apb4_prdata(apb4_prdata)
  );
  assign qspi_spi_sck_o = 1'b0;
  assign qspi_spi_nss_o = 4'hf;
  assign qspi_spi_io_en_o = 4'h0;
  assign qspi_spi_io_out_o = 4'h0;
  assign qspi_irq_o = 1'b0;
endmodule

module apb4_tmr (
  input         apb4_pclk,
  input         apb4_presetn,
  input         apb4_psel,
  input         apb4_penable,
  input         apb4_pwrite,
  input  [31:0] apb4_paddr,
  input  [2:0]  apb4_pprot,
  input  [31:0] apb4_pwdata,
  input  [3:0]  apb4_pstrb,
  output        apb4_pready,
  output        apb4_pslverr,
  output [31:0] apb4_prdata,
  input         tmr_exclk_i,
  input         tmr_capch_i,
  output        tmr_irq_o
);
  easy_box_apb4_base u_base (
    .apb4_pclk(apb4_pclk),
    .apb4_presetn(apb4_presetn),
    .apb4_psel(apb4_psel),
    .apb4_penable(apb4_penable),
    .apb4_pwrite(apb4_pwrite),
    .apb4_paddr(apb4_paddr),
    .apb4_pprot(apb4_pprot),
    .apb4_pwdata(apb4_pwdata),
    .apb4_pstrb(apb4_pstrb),
    .apb4_pready(apb4_pready),
    .apb4_pslverr(apb4_pslverr),
    .apb4_prdata(apb4_prdata)
  );
  assign tmr_irq_o = 1'b0;
endmodule

module apb4_uart (
  input         apb4_pclk,
  input         apb4_presetn,
  input         apb4_psel,
  input         apb4_penable,
  input         apb4_pwrite,
  input  [31:0] apb4_paddr,
  input  [2:0]  apb4_pprot,
  input  [31:0] apb4_pwdata,
  input  [3:0]  apb4_pstrb,
  output        apb4_pready,
  output        apb4_pslverr,
  output [31:0] apb4_prdata,
  input         uart_uart_rx_i,
  output        uart_uart_tx_o,
  output        uart_irq_o
);
  easy_box_apb4_base u_base (
    .apb4_pclk(apb4_pclk),
    .apb4_presetn(apb4_presetn),
    .apb4_psel(apb4_psel),
    .apb4_penable(apb4_penable),
    .apb4_pwrite(apb4_pwrite),
    .apb4_paddr(apb4_paddr),
    .apb4_pprot(apb4_pprot),
    .apb4_pwdata(apb4_pwdata),
    .apb4_pstrb(apb4_pstrb),
    .apb4_pready(apb4_pready),
    .apb4_pslverr(apb4_pslverr),
    .apb4_prdata(apb4_prdata)
  );
  assign uart_uart_tx_o = 1'b1;
  assign uart_irq_o = 1'b0;
endmodule

module apb4_wdg (
  input         apb4_pclk,
  input         apb4_presetn,
  input         apb4_psel,
  input         apb4_penable,
  input         apb4_pwrite,
  input  [31:0] apb4_paddr,
  input  [2:0]  apb4_pprot,
  input  [31:0] apb4_pwdata,
  input  [3:0]  apb4_pstrb,
  output        apb4_pready,
  output        apb4_pslverr,
  output [31:0] apb4_prdata,
  input         wdg_rtc_clk_i,
  output        wdg_rst_o
);
  easy_box_apb4_base u_base (
    .apb4_pclk(apb4_pclk),
    .apb4_presetn(apb4_presetn),
    .apb4_psel(apb4_psel),
    .apb4_penable(apb4_penable),
    .apb4_pwrite(apb4_pwrite),
    .apb4_paddr(apb4_paddr),
    .apb4_pprot(apb4_pprot),
    .apb4_pwdata(apb4_pwdata),
    .apb4_pstrb(apb4_pstrb),
    .apb4_pready(apb4_pready),
    .apb4_pslverr(apb4_pslverr),
    .apb4_prdata(apb4_prdata)
  );
  assign wdg_rst_o = 1'b1;
endmodule

module ChiplinkBridge (
  input         clock,
  input         reset,
  output        slave_axi4_mem_0_awready,
  input         slave_axi4_mem_0_awvalid,
  input  [3:0]  slave_axi4_mem_0_awid,
  input  [31:0] slave_axi4_mem_0_awaddr,
  input  [7:0]  slave_axi4_mem_0_awlen,
  input  [2:0]  slave_axi4_mem_0_awsize,
  input  [1:0]  slave_axi4_mem_0_awburst,
  output        slave_axi4_mem_0_wready,
  input         slave_axi4_mem_0_wvalid,
  input  [63:0] slave_axi4_mem_0_wdata,
  input  [7:0]  slave_axi4_mem_0_wstrb,
  input         slave_axi4_mem_0_wlast,
  input         slave_axi4_mem_0_bready,
  output        slave_axi4_mem_0_bvalid,
  output [3:0]  slave_axi4_mem_0_bid,
  output [1:0]  slave_axi4_mem_0_bresp,
  output        slave_axi4_mem_0_arready,
  input         slave_axi4_mem_0_arvalid,
  input  [3:0]  slave_axi4_mem_0_arid,
  input  [31:0] slave_axi4_mem_0_araddr,
  input  [7:0]  slave_axi4_mem_0_arlen,
  input  [2:0]  slave_axi4_mem_0_arsize,
  input  [1:0]  slave_axi4_mem_0_arburst,
  input         slave_axi4_mem_0_rready,
  output        slave_axi4_mem_0_rvalid,
  output [3:0]  slave_axi4_mem_0_rid,
  output [63:0] slave_axi4_mem_0_rdata,
  output [1:0]  slave_axi4_mem_0_rresp,
  output        slave_axi4_mem_0_rlast,
  input         mem_axi4_0_awready,
  output        mem_axi4_0_awvalid,
  output [3:0]  mem_axi4_0_awid,
  output [31:0] mem_axi4_0_awaddr,
  output [7:0]  mem_axi4_0_awlen,
  output [2:0]  mem_axi4_0_awsize,
  output [1:0]  mem_axi4_0_awburst,
  input         mem_axi4_0_wready,
  output        mem_axi4_0_wvalid,
  output [63:0] mem_axi4_0_wdata,
  output [7:0]  mem_axi4_0_wstrb,
  output        mem_axi4_0_wlast,
  output        mem_axi4_0_bready,
  input         mem_axi4_0_bvalid,
  input  [3:0]  mem_axi4_0_bid,
  input  [1:0]  mem_axi4_0_bresp,
  input         mem_axi4_0_arready,
  output        mem_axi4_0_arvalid,
  output [3:0]  mem_axi4_0_arid,
  output [31:0] mem_axi4_0_araddr,
  output [7:0]  mem_axi4_0_arlen,
  output [2:0]  mem_axi4_0_arsize,
  output [1:0]  mem_axi4_0_arburst,
  output        mem_axi4_0_rready,
  input         mem_axi4_0_rvalid,
  input  [3:0]  mem_axi4_0_rid,
  input  [63:0] mem_axi4_0_rdata,
  input  [1:0]  mem_axi4_0_rresp,
  input         mem_axi4_0_rlast,
  output        fpga_io_c2b_clk,
  output        fpga_io_c2b_rst,
  output        fpga_io_c2b_send,
  output [7:0]  fpga_io_c2b_data,
  input         fpga_io_b2c_clk,
  input         fpga_io_b2c_rst,
  input         fpga_io_b2c_send,
  input  [7:0]  fpga_io_b2c_data
);
  assign slave_axi4_mem_0_awready = 1'b1;
  assign slave_axi4_mem_0_wready = 1'b1;
  assign slave_axi4_mem_0_bvalid = 1'b0;
  assign slave_axi4_mem_0_bid = 4'h0;
  assign slave_axi4_mem_0_bresp = 2'b00;
  assign slave_axi4_mem_0_arready = 1'b1;
  assign slave_axi4_mem_0_rvalid = 1'b0;
  assign slave_axi4_mem_0_rid = 4'h0;
  assign slave_axi4_mem_0_rdata = 64'h0;
  assign slave_axi4_mem_0_rresp = 2'b00;
  assign slave_axi4_mem_0_rlast = 1'b0;

  assign mem_axi4_0_awvalid = 1'b0;
  assign mem_axi4_0_awid = 4'h0;
  assign mem_axi4_0_awaddr = 32'h0;
  assign mem_axi4_0_awlen = 8'h0;
  assign mem_axi4_0_awsize = 3'b010;
  assign mem_axi4_0_awburst = 2'b01;
  assign mem_axi4_0_wvalid = 1'b0;
  assign mem_axi4_0_wdata = 64'h0;
  assign mem_axi4_0_wstrb = 8'h0;
  assign mem_axi4_0_wlast = 1'b1;
  assign mem_axi4_0_bready = 1'b1;
  assign mem_axi4_0_arvalid = 1'b0;
  assign mem_axi4_0_arid = 4'h0;
  assign mem_axi4_0_araddr = 32'h0;
  assign mem_axi4_0_arlen = 8'h0;
  assign mem_axi4_0_arsize = 3'b010;
  assign mem_axi4_0_arburst = 2'b01;
  assign mem_axi4_0_rready = 1'b1;

  assign fpga_io_c2b_clk = clock;
  assign fpga_io_c2b_rst = reset;
  assign fpga_io_c2b_send = 1'b0;
  assign fpga_io_c2b_data = 8'h0;
endmodule
