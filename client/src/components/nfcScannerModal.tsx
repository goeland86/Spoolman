import { WifiOutlined } from "@ant-design/icons";
import { useTranslate } from "@refinedev/core";
import { Alert, Button, FloatButton, Modal, Segmented, Space, Spin, Typography } from "antd";
import React, { useCallback, useState } from "react";
import { useNavigate } from "react-router";
import { isWebNfcSupported, useNfcRead, useNfcStatus } from "../utils/nfc";

const { Text } = Typography;

const NfcScannerModal: React.FC = () => {
  const [visible, setVisible] = useState(false);
  const [mode, setMode] = useState<"browser" | "server">("server");
  const [browserScanning, setBrowserScanning] = useState(false);
  const [browserError, setBrowserError] = useState<string | null>(null);
  const t = useTranslate();
  const navigate = useNavigate();

  const nfcStatus = useNfcStatus();
  const nfcReadMutation = useNfcRead();

  const serverEnabled = nfcStatus.data?.enabled === true && nfcStatus.data?.status === "connected";
  const webNfcAvailable = isWebNfcSupported();

  const handleServerRead = useCallback(async () => {
    const result = await nfcReadMutation.mutateAsync();
    if (result.success && result.spool_id) {
      setVisible(false);
      navigate(`/spool/show/${result.spool_id}`);
    }
  }, [nfcReadMutation, navigate]);

  const handleBrowserScan = useCallback(async () => {
    if (!window.NDEFReader) {
      setBrowserError(t("nfc.error.not_supported"));
      return;
    }

    setBrowserScanning(true);
    setBrowserError(null);

    try {
      const reader = new window.NDEFReader();
      const controller = new AbortController();

      reader.onreading = (event: NDEFReadingEvent) => {
        controller.abort();
        setBrowserScanning(false);

        // Look through NDEF records for a Spoolman URI or ID
        for (const record of event.message.records) {
          if (record.recordType === "url" || record.recordType === "text") {
            const decoder = new TextDecoder(record.encoding || "utf-8");
            const text = record.data ? decoder.decode(record.data) : "";

            // Check for spoolman:s-{id} format
            const spoolmanMatch = text.match(/web\+spoolman:s-(\d+)/);
            if (spoolmanMatch) {
              setVisible(false);
              navigate(`/spool/show/${spoolmanMatch[1]}`);
              return;
            }

            // Check for URL format
            const urlMatch = text.match(/\/spool\/show\/(\d+)/);
            if (urlMatch) {
              setVisible(false);
              navigate(`/spool/show/${urlMatch[1]}`);
              return;
            }
          }
        }

        setBrowserError(t("nfc.no_match"));
      };

      reader.onreadingerror = () => {
        controller.abort();
        setBrowserScanning(false);
        setBrowserError(t("nfc.error.read_failed"));
      };

      await reader.scan({ signal: controller.signal });
    } catch (error) {
      setBrowserScanning(false);
      if (error instanceof DOMException && error.name === "NotAllowedError") {
        setBrowserError(t("nfc.error.permission_denied"));
      } else {
        setBrowserError(t("nfc.error.read_failed"));
      }
    }
  }, [t, navigate]);

  // Don't show the button if neither server NFC nor Web NFC is available
  if (!serverEnabled && !webNfcAvailable) {
    return null;
  }

  return (
    <>
      <FloatButton
        type="primary"
        onClick={() => setVisible(true)}
        icon={<WifiOutlined />}
        shape="circle"
        style={{ insetInlineEnd: 74 }}
      />
      <Modal
        open={visible}
        destroyOnClose
        onCancel={() => {
          setVisible(false);
          setBrowserScanning(false);
          setBrowserError(null);
        }}
        footer={null}
        title={t("nfc.scan_title")}
      >
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Text>{t("nfc.scan_description")}</Text>

          <Segmented
            block
            options={[
              { label: t("nfc.mode_server"), value: "server", disabled: !serverEnabled },
              { label: t("nfc.mode_browser"), value: "browser", disabled: !webNfcAvailable },
            ]}
            value={mode}
            onChange={(value) => setMode(value as "browser" | "server")}
          />

          {mode === "server" && (
            <Space direction="vertical" style={{ width: "100%" }} align="center">
              <Button
                type="primary"
                onClick={handleServerRead}
                loading={nfcReadMutation.isPending}
                size="large"
              >
                {nfcReadMutation.isPending ? t("nfc.reading") : t("nfc.scan_title")}
              </Button>
              {nfcReadMutation.isSuccess && !nfcReadMutation.data?.spool_id && (
                <Alert
                  type="warning"
                  message={nfcReadMutation.data?.message || t("nfc.no_match")}
                  showIcon
                />
              )}
              {nfcReadMutation.isError && (
                <Alert type="error" message={t("nfc.error.read_failed")} showIcon />
              )}
            </Space>
          )}

          {mode === "browser" && (
            <Space direction="vertical" style={{ width: "100%" }} align="center">
              {browserScanning ? (
                <Spin tip={t("nfc.place_tag")}>
                  <div style={{ padding: 50 }} />
                </Spin>
              ) : (
                <Button type="primary" onClick={handleBrowserScan} size="large">
                  {t("nfc.scan_title")}
                </Button>
              )}
              {browserError && <Alert type="error" message={browserError} showIcon />}
            </Space>
          )}
        </Space>
      </Modal>
    </>
  );
};

export default NfcScannerModal;
