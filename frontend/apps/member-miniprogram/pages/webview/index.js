Page({
  data: { url: "", error: "" },
  onLoad(options) {
    const key =
      typeof options.key === "string" ? decodeURIComponent(options.key) : "";
    const handoff = key ? wx.getStorageSync(key) : null;
    if (key) wx.removeStorageSync(key);
    if (!handoff || !/^https?:\/\//i.test(handoff.url || "")) {
      this.setData({ error: "购买渠道地址无效，请返回重试。" });
      return;
    }
    const fragment = handoff.handoff
      ? `#yimatong_handoff=${encodeURIComponent(handoff.handoff)}`
      : "";
    this.setData({ url: `${handoff.url}${fragment}` });
  },
});
