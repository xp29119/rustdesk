use hbb_common::{anyhow, ResultType};
use jni::{
    objects::{GlobalRef, JByteArray, JClass, JObject, JValue},
    JNIEnv, JavaVM,
};
use managed_auth::Vault;
use std::sync::OnceLock;

static CONTEXT: OnceLock<(JavaVM, GlobalRef)> = OnceLock::new();
pub struct AndroidVault;

#[no_mangle]
pub extern "system" fn Java_ffi_FFI_initManagedAuth(
    mut env: JNIEnv,
    _class: JClass,
    store: JObject,
) {
    let result = (|| -> jni::errors::Result<()> {
        let vm = env.get_java_vm()?;
        let store = env.new_global_ref(store)?;
        let _ = CONTEXT.set((vm, store));
        Ok(())
    })();
    if result.is_err() {
        let _ = env.throw_new(
            "java/lang/IllegalStateException",
            "Managed credential storage unavailable",
        );
    }
}

impl Vault for AndroidVault {
    fn load(&self) -> ResultType<Option<Vec<u8>>> {
        let (vm, store) = CONTEXT
            .get()
            .ok_or_else(|| anyhow::anyhow!("Protected storage is not ready"))?;
        let mut env = vm.attach_current_thread()?;
        let result = env.with_local_frame(8, |env| -> jni::errors::Result<Option<Vec<u8>>> {
            let value = env.call_method(store, "load", "()[B", &[])?.l()?;
            if value.is_null() {
                return Ok(None);
            }
            Ok(Some(env.convert_byte_array(JByteArray::from(value))?))
        });
        match result {
            Ok(value) => Ok(value),
            Err(_) => {
                env.exception_clear()?;
                anyhow::bail!("Protected Android login could not be read")
            }
        }
    }

    fn save(&self, value: &[u8]) -> ResultType<()> {
        let (vm, store) = CONTEXT
            .get()
            .ok_or_else(|| anyhow::anyhow!("Protected storage is not ready"))?;
        let mut env = vm.attach_current_thread()?;
        let result = env.with_local_frame(8, |env| -> jni::errors::Result<()> {
            let value = JObject::from(env.byte_array_from_slice(value)?);
            env.call_method(store, "save", "([B)V", &[JValue::Object(&value)])?;
            Ok(())
        });
        match result {
            Ok(()) => Ok(()),
            Err(_) => {
                env.exception_clear()?;
                anyhow::bail!("Could not securely save Android login")
            }
        }
    }
}
