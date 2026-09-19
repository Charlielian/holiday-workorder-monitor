# -*- coding: utf-8 -*-
"""
加密工具模块
提供RSA加密、AES加密等加密功能
"""

import base64
import logging

logger = logging.getLogger(__name__)

try:
    from Crypto.PublicKey import RSA
    from Crypto.Cipher import PKCS1_v1_5
    from Crypto.Cipher import AES as AES_Cipher
    from Crypto.Util.Padding import pad, unpad
except ModuleNotFoundError:
    from Cryptodome.PublicKey import RSA
    from Cryptodome.Cipher import PKCS1_v1_5
    from Cryptodome.Cipher import AES as AES_Cipher
    from Cryptodome.Util.Padding import pad, unpad


def rsa_encrypt(data, public_key):
    """RSA加密

    Args:
        data: 待加密字符串
        public_key: RSA公钥（PEM格式字符串，去除头尾）

    Returns:
        str: Base64编码的加密数据
    """
    if not public_key or not data:
        raise ValueError("公钥和数据不能为空")
    public_key = '-----BEGIN PUBLIC KEY-----\n' + public_key + '\n-----END PUBLIC KEY-----'
    rsa_key = RSA.import_key(public_key)
    cipher = PKCS1_v1_5.new(rsa_key)
    encrypted_data = base64.b64encode(cipher.encrypt(data.encode(encoding="utf-8")))
    return encrypted_data.decode('utf-8')


def aes_encrypt(plain_text, key):
    """AES加密（CBC模式，PKCS7填充）

    Args:
        plain_text: 明文字符串
        key: AES密钥（16/24/32字节）

    Returns:
        bytes: IV + 密文
    """
    import os
    iv = os.urandom(16)
    cipher = AES_Cipher.new(key, AES_Cipher.MODE_CBC, iv)
    padded_data = pad(plain_text.encode("utf-8"), 16)
    encrypted_data = cipher.encrypt(padded_data)
    return iv + encrypted_data


def aes_decrypt(encrypted_data, key):
    """AES解密（CBC模式，PKCS7填充）

    Args:
        encrypted_data: IV + 密文
        key: AES密钥（16/24/32字节）

    Returns:
        str: 解密后的字符串
    """
    if len(encrypted_data) < 17:
        raise ValueError("加密数据太短，无法包含IV")
    iv = encrypted_data[:16]
    cipher = AES_Cipher.new(key, AES_Cipher.MODE_CBC, iv)
    decrypted_data = cipher.decrypt(encrypted_data[16:])
    return unpad(decrypted_data, 16).decode('utf-8')


def rsa_sign(data, private_key):
    """RSA签名（SHA256）

    Args:
        data: 待签名数据
        private_key: RSA私钥

    Returns:
        str: Base64编码的签名
    """
    from Crypto.Hash import SHA256
    from Crypto.Signature import pkcs1_15
    h = SHA256.new(data.encode("utf-8"))
    signature = pkcs1_15.new(private_key).sign(h)
    return base64.b64encode(signature).decode('utf-8')


def rsa_verify(data, signature, public_key):
    """RSA验签（SHA256）

    Args:
        data: 原始数据
        signature: Base64编码的签名
        public_key: RSA公钥

    Returns:
        bool: 验签是否成功
    """
    from Crypto.Hash import SHA256
    from Crypto.Signature import pkcs1_15
    try:
        public_key = '-----BEGIN PUBLIC KEY-----\n' + public_key + '\n-----END PUBLIC KEY-----'
        rsa_key = RSA.import_key(public_key)
        h = SHA256.new(data.encode("utf-8"))
        pkcs1_15.new(rsa_key).verify(h, base64.b64decode(signature))
        return True
    except Exception as e:
        logger.debug(f"RSA验签失败: {e}")
        return False
