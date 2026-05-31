"use client";

import { Form, Input, InputNumber, Divider, Radio, Select } from "antd";
import ImageUploadInput from "@/components/ImageUploadInput";
import { YuanInput } from "./YuanInput";
import { AMOUNT_TYPE_OPTIONS } from "./constants";
import type { Connector } from "./types";

function CashRedPacketConfigFields({
  form,
  connectors,
}: {
  form: ReturnType<typeof Form.useForm>[0];
  connectors: Connector[];
}) {
  const amountType = Form.useWatch(["config_json", "amount_type"], form) ?? "";

  return (
    <>
      <Divider titlePlacement="left" plain>红包金额设置</Divider>
      <Form.Item name={["config_json", "amount_type"]} label="金额类型" rules={[{ required: true, message: "请选择金额类型" }]}>
        <Radio.Group options={AMOUNT_TYPE_OPTIONS} optionType="button" buttonStyle="solid" />
      </Form.Item>
      {amountType === "fixed" && (
        <Form.Item name={["config_json", "fixed_amount"]} label="固定金额" rules={[{ required: true, message: "请输入固定金额" }]} extra="微信现金营销单笔上限 200 元">
          <YuanInput max={200} />
        </Form.Item>
      )}
      {amountType === "random" && (
        <>
          <Form.Item name={["config_json", "min_amount"]} label="最小金额" rules={[{ required: true, message: "请输入最小金额" }]}><YuanInput max={200} /></Form.Item>
          <Form.Item name={["config_json", "max_amount"]} label="最大金额" rules={[{ required: true, message: "请输入最大金额" }]}><YuanInput max={200} /></Form.Item>
        </>
      )}
      {amountType === "lucky" && (
        <>
          <Form.Item name={["config_json", "lucky_total_count"]} label="拼手气总份数" rules={[{ required: true, message: "请输入拼手气总份数" }]}>
            <InputNumber min={2} max={100} className="w-full" addonAfter="份" />
          </Form.Item>
          <Form.Item name={["config_json", "lucky_min_per"]} label="每人最小金额" rules={[{ required: true, message: "请输入每人最小金额" }]}><YuanInput max={200} /></Form.Item>
        </>
      )}
      <Divider titlePlacement="left" plain>预算与限制</Divider>
      <Form.Item name={["config_json", "budget"]} label="总预算" rules={[{ required: true, message: "请输入总预算" }]} extra="红包发放的总预算金额"><YuanInput /></Form.Item>
      <div className="grid grid-cols-2 gap-4">
        <Form.Item name={["config_json", "daily_limit_per_user"]} label="每用户每日限领" initialValue={3}>
          <InputNumber min={1} max={100} className="w-full" addonAfter="次" />
        </Form.Item>
        <Form.Item name={["config_json", "total_limit_per_user"]} label="每用户总限领" initialValue={10}>
          <InputNumber min={1} max={1000} className="w-full" addonAfter="次" />
        </Form.Item>
      </div>
      <Form.Item name={["config_json", "transfer_remark"]} label="转账备注" extra="微信转账到零钱时显示的备注（选填）">
        <Input placeholder="扫码领红包" maxLength={32} />
      </Form.Item>
      <Divider titlePlacement="left" plain>微信支付连接器</Divider>
      <Form.Item name="connector_id" label="微信支付转账连接器" rules={[{ required: true, message: "请选择微信支付转账连接器" }]} extra="用于调用微信支付商家转账到零钱 API">
        <Select placeholder="选择已配置的微信支付转账连接器" showSearch optionFilterProp="label"
          notFoundContent={connectors.length === 0 ? "暂无微信支付转账连接器，请先在连接器管理中创建" : undefined}
          options={connectors.map((c) => ({ value: c.id, label: `${c.name}${c.enabled ? "" : "（已禁用）"}` }))}
        />
      </Form.Item>
    </>
  );
}

export function BenefitConfigFields({
  benefitType,
  form,
  couponPoolConnectors,
  wechatPayConnectors,
}: {
  benefitType: string;
  form: ReturnType<typeof Form.useForm>[0];
  couponPoolConnectors: Connector[];
  wechatPayConnectors: Connector[];
}) {
  if (benefitType === "cash_red_packet") {
    return <CashRedPacketConfigFields form={form} connectors={wechatPayConnectors} />;
  }
  if (benefitType === "platform_coupon") {
    return (
      <>
        <Form.Item name={["config_json", "amount"]} label="券面额（元）"><InputNumber min={0} className="w-full" /></Form.Item>
        <Form.Item name={["config_json", "min_order"]} label="最低订单金额（元）"><InputNumber min={0} className="w-full" /></Form.Item>
        <Form.Item name={["config_json", "coupon_code"]} label="券码"><Input placeholder="可选，留空则系统自动生成" /></Form.Item>
        <Divider titlePlacement="left" plain>券码池连接器</Divider>
        <Form.Item name="connector_id" label="券码池连接器" extra="关联券码池后，消费者领取时自动分配券码">
          <Select placeholder="选择券码池连接器（可选）" showSearch optionFilterProp="label" allowClear
            notFoundContent={couponPoolConnectors.length === 0 ? "暂无券码池连接器，请先在连接器管理中创建" : undefined}
            options={couponPoolConnectors.map((c) => ({ value: c.id, label: `${c.name}${c.enabled ? "" : "（已禁用）"}` }))}
          />
        </Form.Item>
      </>
    );
  }
  if (benefitType === "external_link") {
    return (
      <>
        <Form.Item name={["config_json", "url"]} label="跳转链接"><Input placeholder="https://example.com" /></Form.Item>
        <Form.Item name={["config_json", "link_text"]} label="链接文案"><Input placeholder="点击领取" /></Form.Item>
      </>
    );
  }
  if (benefitType === "private_domain") {
    return (
      <>
        <Form.Item
          name={["config_json", "qr_image_url"]}
          label="微信群二维码图片"
          extra="消费者领取后展示。可直接上传二维码图片，也可粘贴公开图片链接。"
          rules={[{ type: "url", message: "请输入以 http:// 或 https:// 开头的图片链接" }]}
        >
          <ImageUploadInput module="benefit-qr" buttonText="上传二维码" previewAlt="微信群二维码预览" />
        </Form.Item>
        <Form.Item name={["config_json", "group_name"]} label="群名称"><Input /></Form.Item>
      </>
    );
  }
  if (benefitType === "form_benefit") {
    return (
      <>
        <Form.Item name={["config_json", "form_url"]} label="表单链接"><Input placeholder="https://..." /></Form.Item>
        <Form.Item name={["config_json", "require_phone"]} label="需要手机号">
          <Select options={[{ value: true, label: "是" }, { value: false, label: "否" }]} />
        </Form.Item>
      </>
    );
  }
  return null;
}
