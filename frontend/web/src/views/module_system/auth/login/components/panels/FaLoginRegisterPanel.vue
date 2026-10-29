<!-- 注册表单 -->
<template>
  <div>
    <ElForm
      ref="formRef"
      :model="registerForm"
      :rules="registerRules"
      :key="formKey"
      class="login-page-form"
      @keyup.enter="$emit('submit')"
    >
      <ElFormItem prop="username">
        <ElInput
          class="custom-height"
          v-model.trim="registerForm.username"
          clearable
          :placeholder="$t('login.placeholder.username')"
          :aria-label="$t('login.placeholder.username')"
        >
          <template #prefix>
            <ElIcon><FaSvgIcon icon="ri:user-3-line" /></ElIcon>
          </template>
        </ElInput>
      </ElFormItem>
      <ElFormItem prop="name">
        <ElInput
          class="custom-height"
          v-model.trim="registerForm.name"
          clearable
          :placeholder="$t('register.placeholder.name')"
          :aria-label="$t('register.placeholder.name')"
        >
          <template #prefix>
            <ElIcon><FaSvgIcon icon="ri:user-3-line" /></ElIcon>
          </template>
        </ElInput>
      </ElFormItem>
      <ElTooltip :visible="isCapsLock" :content="$t('login.capsLock')" placement="right">
        <ElFormItem prop="password">
          <ElInput
            class="custom-height"
            v-model.trim="registerForm.password"
            type="password"
            autocomplete="off"
            show-password
            clearable
            :placeholder="$t('login.placeholder.password')"
            @keyup="checkCapsLock"
            :aria-label="$t('login.placeholder.password')"
          >
            <template #prefix>
              <ElIcon><FaSvgIcon icon="ri:lock-2-line" /></ElIcon>
            </template>
          </ElInput>
        </ElFormItem>
      </ElTooltip>
      <ElTooltip :visible="isCapsLock" :content="$t('login.capsLock')" placement="right">
        <ElFormItem prop="confirmPassword">
          <ElInput
            class="custom-height"
            v-model.trim="registerForm.confirmPassword"
            type="password"
            autocomplete="off"
            show-password
            clearable
            :placeholder="$t('login.message.password.confirm')"
            @keyup="checkCapsLock"
            :aria-label="$t('login.message.password.confirm')"
          >
            <template #prefix>
              <ElIcon><FaSvgIcon icon="ri:lock-2-line" /></ElIcon>
            </template>
          </ElInput>
        </ElFormItem>
      </ElTooltip>
      <ElFormItem>
        <div class="flex flex-wrap items-center gap-2">
          <ElCheckbox v-model="registerAgreementReadModel">
            {{ $t("login.agree") }}
          </ElCheckbox>
          <ElLink
            type="primary"
            underline="never"
            class="text-sm font-medium"
            :href="userAgreementHref"
            target="_blank"
            rel="noopener noreferrer"
          >
            {{ $t("login.userAgreement") }}
          </ElLink>
        </div>
      </ElFormItem>
      <div class="mt-6">
        <ElButton
          class="h-11 w-full rounded-lg! text-base font-medium"
          type="primary"
          :loading="registerLoading"
          v-ripple
          @click="$emit('submit')"
        >
          {{ $t("login.register") }}
        </ElButton>
      </div>
    </ElForm>

    <FaLoginAuthLinkRow
      :hint="$t('login.haveAccount')"
      :link-text="$t('login.backLoginBtnText')"
      @link="$emit('toLogin')"
    />
  </div>
</template>

<script setup lang="ts">
import type { RegisterForm } from "@/api/module_system/user";
import type { FormRules } from "element-plus";

const registerForm = defineModel<RegisterForm>("registerForm", { required: true });

defineOptions({ name: "FaLoginRegisterPanel" });

interface Props {
  registerRules: FormRules<RegisterForm>;
  formKey: number | string;
  registerLoading: boolean;
  userAgreementHref: string;
}

withDefaults(defineProps<Props>(), {});

const registerAgreementReadModel = defineModel<boolean>("registerAgreementRead", {
  required: true,
});

interface Emits {
  submit: [];
  toLogin: [];
}

const emit = defineEmits<Emits>();

const formRef = ref();
const isCapsLock = ref(false);

function checkCapsLock(event: KeyboardEvent) {
  if (event instanceof KeyboardEvent) {
    isCapsLock.value = event.getModifierState("CapsLock");
    if (event.key === "Enter") {
      emit("submit");
    }
  }
}

defineExpose({
  validate: () => formRef.value?.validate?.(),
  clearValidate: () => formRef.value?.clearValidate?.(),
  validateField: (prop: string | string[]) => formRef.value?.validateField?.(prop),
});
</script>
