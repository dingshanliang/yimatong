import Link from "next/link";
import { Button, Result } from "antd";

export default function NotFound() {
  return (
    <div className="flex min-h-screen items-center justify-center px-4" style={{ background: "var(--admin-bg-layout)" }}>
      <Result
        status="404"
        title="页面不存在"
        subTitle="请检查地址是否正确，或返回工作台继续操作。"
        extra={
          <Link href="/">
            <Button type="primary">返回工作台</Button>
          </Link>
        }
      />
    </div>
  );
}
