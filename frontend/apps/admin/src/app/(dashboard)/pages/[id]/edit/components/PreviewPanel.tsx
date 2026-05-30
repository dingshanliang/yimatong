"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { Select, Slider, Typography } from "antd";
import {
  MobileOutlined,
  DesktopOutlined,
} from "@ant-design/icons";
import type { PageDSL } from "@/lib/page-dsl";

const { Text } = Typography;

type DevicePreset = {
  label: string;
  width: number;
  height: number;
  icon: React.ReactNode;
};

const DEVICE_PRESETS: Record<string, DevicePreset> = {
  iphone15: { label: "iPhone 15", width: 393, height: 852, icon: <MobileOutlined /> },
  iphone_se: { label: "iPhone SE", width: 375, height: 667, icon: <MobileOutlined /> },
  desktop: { label: "桌面", width: 1024, height: 768, icon: <DesktopOutlined /> },
};

export function PreviewPanel({ dsl }: { dsl: PageDSL }) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [device, setDevice] = useState("iphone15");
  const [scale, setScale] = useState(75);
  const [ready, setReady] = useState(false);

  const preset = DEVICE_PRESETS[device];
  const h5Url = process.env.NEXT_PUBLIC_H5_URL || "http://localhost:3001";

  const sendDSL = useCallback(() => {
    if (!iframeRef.current?.contentWindow || !ready) return;
    iframeRef.current.contentWindow.postMessage(
      { type: "preview-dsl", payload: dsl },
      h5Url,
    );
  }, [dsl, ready, h5Url]);

  useEffect(() => {
    sendDSL();
  }, [sendDSL]);

  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.data?.type === "preview-ready") {
        setReady(true);
      }
    }
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, []);

  useEffect(() => {
    if (ready) sendDSL();
  }, [ready, sendDSL]);

  return (
    <div className="flex h-full flex-col items-center">
      <div className="mb-3 flex w-full items-center justify-between border-b pb-2">
        <Select
          size="small"
          value={device}
          onChange={setDevice}
          options={Object.entries(DEVICE_PRESETS).map(([key, p]) => ({
            value: key,
            label: (
              <span className="flex items-center gap-1">
                {p.icon} {p.label}
              </span>
            ),
          }))}
          style={{ width: 150 }}
        />
        <div className="flex items-center gap-2">
          <Text type="secondary" className="text-xs">缩放</Text>
          <Slider
            min={50}
            max={100}
            value={scale}
            onChange={setScale}
            style={{ width: 100 }}
          />
          <Text type="secondary" className="text-xs">{scale}%</Text>
        </div>
      </div>

      <div
        className="flex-1 flex items-start justify-center overflow-auto"
        style={{ padding: "8px" }}
      >
        <div
          style={{
            width: preset.width * (scale / 100) + 24,
            height: preset.height * (scale / 100) + 24,
            border: "3px solid #333",
            borderRadius: 24,
            padding: 8,
            background: "#fff",
            overflow: "hidden",
          }}
        >
          <iframe
            ref={iframeRef}
            src={`${h5Url}/preview`}
            style={{
              width: preset.width,
              height: preset.height,
              border: "none",
              transform: `scale(${scale / 100})`,
              transformOrigin: "top left",
            }}
            title="H5 预览"
          />
        </div>
      </div>
    </div>
  );
}