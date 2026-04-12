const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PHONE_REGEX = /^(?:\+?86)?1\d{10}$/;
const PASSWORD_REGEX = /^(?=.*[A-Za-z])(?=.*\d)(?=.*[*\-@_])[A-Za-z\d*\-@_]{8,}$/;

export function isEmail(value) {
  return EMAIL_REGEX.test(value);
}

export function isPhone(value) {
  return PHONE_REGEX.test(value);
}

export function isEmailOrPhone(value) {
  return isEmail(value) || isPhone(value);
}

export function isStrongPassword(value) {
  return PASSWORD_REGEX.test(value);
}
