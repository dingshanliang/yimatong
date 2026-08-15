"use client";

import { useCallback, useRef, useState } from "react";
import { Form, Input, Modal, Typography } from "antd";

const { Text } = Typography;

export function newExportIdempotencyKey(): string {
  return globalThis.crypto.randomUUID();
}

export function useExportReasonDialog() {
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<{ reason: string }>();
  const resolver = useRef<((reason: string | null) => void) | null>(null);

  const requestReason = useCallback(() => {
    resolver.current?.(null);
    form.resetFields();
    setOpen(true);
    return new Promise<string | null>((resolve) => {
      resolver.current = resolve;
    });
  }, [form]);

  const finish = useCallback((reason: string | null) => {
    setOpen(false);
    resolver.current?.(reason);
    resolver.current = null;
  }, []);

  const dialog = (
    <Modal
      title="填写导出原因"
      open={open}
      okText="准备导出"
      cancelText="取消"
      width={440}
      onCancel={() => finish(null)}
      onOk={async () => {
        const values = await form.validateFields();
        finish(values.reason.trim());
      }}
      destroyOnHidden
    >
      <Text type="secondary">原因会随导出范围和文件摘要写入审计记录。</Text>
      <Form form={form} layout="vertical" className="mt-3">
        <Form.Item
          name="reason"
          label="导出原因"
          rules={[
            { required: true, whitespace: true, message: "请输入导出原因" },
            { max: 500, message: "导出原因不能超过 500 个字符" },
          ]}
        >
          <Input.TextArea
            autoFocus
            autoSize={{ minRows: 2, maxRows: 4 }}
            maxLength={500}
            showCount
            placeholder="例如：月度经营复盘"
          />
        </Form.Item>
      </Form>
    </Modal>
  );

  return { requestReason, exportReasonDialog: dialog };
}
