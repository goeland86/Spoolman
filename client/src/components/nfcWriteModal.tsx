import { useTranslate } from "@refinedev/core";
import { Alert, Descriptions, Input, Modal, Segmented, Space, Spin, Typography } from "antd";
import React, { useCallback, useState } from "react";
import { isWebNfcSupported, useNfcStatus, useNfcWrite } from "../utils/nfc";
import { ISpool } from "../pages/spools/model";

const { Text } = Typography;

interface NfcWriteModalProps {
  spool?: ISpool;
  visible: boolean;
  onClose: () => void;
}

const NfcWriteModal: React.FC<NfcWriteModalProps> = ({ spool, visible, onClose }) => {
  const [mode, setMode] = useState<"browser" | "server">("server");
  const [userMessage, setUserMessage] = useState("");
  const [browserWriting, setBrowserWriting] = useState(false);
  const [browserResult, setBrowserResult] = useState<{ success: boolean; message: string } | null>(null);
  const t = useTranslate();

  const nfcStatus = useNfcStatus();
  const nfcWriteMutation = useNfcWrite();

  const serverEnabled = nfcStatus.data?.enabled === true && nfcStatus.data?.status === "connected";
  const webNfcAvailable = isWebNfcSupported();

  const handleServerWrite = useCallback(async () => {
    if (!spool) return;

    const result = await nfcWriteMutation.mutateAsync({
      spool_id: spool.id,
      user_message: userMessage,
    });

    // Result is handled by mutation state
  }, [spool, userMessage, nfcWriteMutation]);

  const handleBrowserWrite = useCallback(async () => {
    if (!spool || !window.NDEFReader) {
      setBrowserResult({ success: false, message: t("nfc.error.not_supported") });
      return;
    }

    setBrowserWriting(true);
    setBrowserResult(null);

    try {
      const reader = new window.NDEFReader();

      // Write a URI record that Spoolman can recognize
      await reader.write({
        records: [
          {
            recordType: "url",
            data: `web+spoolman:s-${spool.id}`,
          },
        ],
      });

      setBrowserWriting(false);
      setBrowserResult({ success: true, message: t("nfc.write_success") });
    } catch (error) {
      setBrowserWriting(false);
      if (error instanceof DOMException && error.name === "NotAllowedError") {
        setBrowserResult({ success: false, message: t("nfc.error.permission_denied") });
      } else {
        setBrowserResult({ success: false, message: t("nfc.write_error") });
      }
    }
  }, [spool, t]);

  const handleOk = () => {
    if (mode === "server") {
      handleServerWrite();
    } else {
      handleBrowserWrite();
    }
  };

  const filament = spool?.filament;

  return (
    <Modal
      title={t("nfc.encode_title")}
      open={visible}
      onOk={handleOk}
      onCancel={() => {
        onClose();
        setBrowserResult(null);
        setUserMessage("");
      }}
      okText={nfcWriteMutation.isPending || browserWriting ? t("nfc.writing") : t("nfc.encode_button")}
      okButtonProps={{ loading: nfcWriteMutation.isPending || browserWriting }}
      destroyOnClose
    >
      <Space direction="vertical" style={{ width: "100%" }} size="middle">
        <Segmented
          block
          options={[
            { label: t("nfc.mode_server"), value: "server", disabled: !serverEnabled },
            { label: t("nfc.mode_browser"), value: "browser", disabled: !webNfcAvailable },
          ]}
          value={mode}
          onChange={(value) => setMode(value as "browser" | "server")}
        />

        {filament && (
          <>
            <Text strong>{t("nfc.preview_title")}</Text>
            <Descriptions column={1} size="small" bordered>
              {filament.vendor && (
                <Descriptions.Item label={t("filament.fields.vendor")}>{filament.vendor.name}</Descriptions.Item>
              )}
              {filament.name && (
                <Descriptions.Item label={t("filament.fields.name")}>{filament.name}</Descriptions.Item>
              )}
              {filament.material && (
                <Descriptions.Item label={t("filament.fields.material")}>{filament.material}</Descriptions.Item>
              )}
              <Descriptions.Item label={t("filament.fields.diameter")}>{filament.diameter} mm</Descriptions.Item>
              {filament.color_hex && (
                <Descriptions.Item label={t("filament.fields.color_hex")}>
                  <span
                    style={{
                      display: "inline-block",
                      width: 16,
                      height: 16,
                      backgroundColor: `#${filament.color_hex}`,
                      border: "1px solid #ccc",
                      marginRight: 8,
                      verticalAlign: "middle",
                    }}
                  />
                  #{filament.color_hex}
                </Descriptions.Item>
              )}
              {filament.weight && (
                <Descriptions.Item label={t("filament.fields.weight")}>{filament.weight} g</Descriptions.Item>
              )}
              {filament.settings_extruder_temp && (
                <Descriptions.Item label={t("filament.fields.settings_extruder_temp")}>
                  {filament.settings_extruder_temp} °C
                </Descriptions.Item>
              )}
              {filament.settings_bed_temp && (
                <Descriptions.Item label={t("filament.fields.settings_bed_temp")}>
                  {filament.settings_bed_temp} °C
                </Descriptions.Item>
              )}
            </Descriptions>
          </>
        )}

        {mode === "server" && (
          <div>
            <Text>{t("nfc.user_message")}</Text>
            <Input
              value={userMessage}
              onChange={(e) => setUserMessage(e.target.value.slice(0, 28))}
              maxLength={28}
              placeholder={t("nfc.user_message_help")}
            />
          </div>
        )}

        {mode === "server" && (nfcWriteMutation.isPending || browserWriting) && (
          <Spin tip={t("nfc.place_tag")}>
            <div style={{ padding: 30 }} />
          </Spin>
        )}

        {mode === "server" && nfcWriteMutation.isSuccess && (
          <Alert
            type={nfcWriteMutation.data?.success ? "success" : "error"}
            message={nfcWriteMutation.data?.message}
            showIcon
          />
        )}

        {mode === "server" && nfcWriteMutation.isError && (
          <Alert type="error" message={t("nfc.write_error")} showIcon />
        )}

        {mode === "browser" && browserWriting && (
          <Spin tip={t("nfc.place_tag")}>
            <div style={{ padding: 30 }} />
          </Spin>
        )}

        {mode === "browser" && browserResult && (
          <Alert
            type={browserResult.success ? "success" : "error"}
            message={browserResult.message}
            showIcon
          />
        )}

        {mode === "browser" && (
          <Text type="secondary">
            {t("nfc.scan_description")}
          </Text>
        )}
      </Space>
    </Modal>
  );
};

export default NfcWriteModal;
