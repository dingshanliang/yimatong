"use client";

import { useEffect, useState } from "react";
import { Button, Card, Modal, Steps, Typography, Tag } from "antd";
import {
  AppstoreOutlined,
  QrcodeOutlined,
  FileTextOutlined,
  GiftOutlined,
  RocketOutlined,
  CheckCircleOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import api from "@/lib/api";

const { Title, Text } = Typography;

interface OnboardingData {
  steps: string[];
  completed_steps: string[];
  current_step: string | null;
  is_complete: boolean;
}

const STEP_CONFIG: Record<
  string,
  { title: string; desc: string; icon: React.ReactNode; link: string }
> = {
  create_product: {
    title: "创建产品",
    desc: "录入您的第一个产品信息，包括品牌、品类、溯源故事等",
    icon: <AppstoreOutlined />,
    link: "/products",
  },
  create_batch: {
    title: "创建生产批次",
    desc: "为产品创建生产批次，生成溯源码",
    icon: <QrcodeOutlined />,
    link: "/batches",
  },
  create_page: {
    title: "配置扫码页面",
    desc: "设计消费者扫码后看到的 H5 页面",
    icon: <FileTextOutlined />,
    link: "/pages",
  },
  create_campaign: {
    title: "创建营销活动",
    desc: "设置红包、优惠券等营销活动，吸引消费者参与",
    icon: <GiftOutlined />,
    link: "/campaigns",
  },
  activate: {
    title: "激活上线",
    desc: "发布页面、激活码批次，让消费者可以扫码体验",
    icon: <RocketOutlined />,
    link: "/codes",
  },
};

export default function OnboardingWizard() {
  const router = useRouter();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<OnboardingData | null>(null);

  useEffect(() => {
    // Check if user has dismissed wizard in this session
    if (sessionStorage.getItem("onboarding_dismissed") === "1") {
      return;
    }

    const fetchProgress = async () => {
      try {
        const { data: resp } = await api.get<OnboardingData>("/tenants/me/onboarding");
        setData(resp);
        if (!resp.is_complete) {
          setVisible(true);
        }
      } catch {
        // Silently fail — wizard is not critical
      }
    };

    fetchProgress();
  }, []);

  const markStepDone = async (step: string) => {
    setLoading(true);
    try {
      await api.post(`/tenants/me/onboarding/step/${step}`);
      // Refresh progress
      const { data: resp } = await api.get<OnboardingData>("/tenants/me/onboarding");
      setData(resp);
      if (resp.is_complete) {
        setVisible(false);
      }
    } catch {
      // Ignore error
    } finally {
      setLoading(false);
    }
  };

  const handleDismiss = () => {
    setVisible(false);
    sessionStorage.setItem("onboarding_dismissed", "1");
  };

  if (!visible || !data || data.is_complete) return null;

  const currentIndex = data.steps.findIndex((s) => s === data.current_step);
  const activeStep = currentIndex >= 0 ? currentIndex : data.steps.length;

  return (
    <Modal
      open={visible}
      onCancel={handleDismiss}
      footer={null}
      width={720}
      closable
      maskClosable
      title={
        <div className="flex items-center gap-2">
          <RocketOutlined className="text-primary" />
          <span>欢迎使用一码通！完成初始化向导，快速上线您的首个扫码活动</span>
        </div>
      }
    >
      <div className="py-4">
        <Steps
          current={activeStep}
          size="small"
          direction="horizontal"
          items={data.steps.map((stepKey) => {
            const cfg = STEP_CONFIG[stepKey];
            const isDone = data.completed_steps.includes(stepKey);
            return {
              title: cfg?.title ?? stepKey,
              icon: isDone ? <CheckCircleOutlined /> : cfg?.icon,
            };
          })}
        />

        <div className="mt-6 grid grid-cols-1 gap-4">
          {data.steps.map((stepKey, idx) => {
            const cfg = STEP_CONFIG[stepKey];
            const isDone = data.completed_steps.includes(stepKey);
            const isCurrent = idx === activeStep;

            if (!cfg) return null;

            return (
              <Card
                key={stepKey}
                size="small"
                className={`transition-all ${
                  isCurrent ? "border-primary ring-1 ring-primary/20" : ""
                } ${isDone ? "opacity-60" : ""}`}
                title={
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      {cfg.icon}
                      <span>{cfg.title}</span>
                    </div>
                    {isDone && <Tag color="success">已完成</Tag>}
                    {isCurrent && <Tag color="processing">当前步骤</Tag>}
                  </div>
                }
              >
                <div className="flex items-center justify-between">
                  <Text type="secondary" className="max-w-md">
                    {cfg.desc}
                  </Text>
                  <div className="flex gap-2">
                    {!isDone && (
                      <Button
                        type={isCurrent ? "primary" : "default"}
                        size="small"
                        onClick={() => router.push(cfg.link)}
                      >
                        去配置
                      </Button>
                    )}
                    {isCurrent && (
                      <Button
                        size="small"
                        loading={loading}
                        onClick={() => markStepDone(stepKey)}
                      >
                        标记完成
                      </Button>
                    )}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>

        <div className="mt-4 flex justify-end">
          <Button type="link" onClick={handleDismiss}>
            稍后再说
          </Button>
        </div>
      </div>
    </Modal>
  );
}
