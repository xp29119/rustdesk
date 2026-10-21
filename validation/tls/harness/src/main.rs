use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{error::Error, io::{self, Read}};

include!(concat!(env!("OUT_DIR"), "/production_source.rs"));

fn error_details(error: &(dyn Error + 'static)) -> Vec<String> {
    let mut messages = vec![error.to_string()];
    let mut cause = error.source();
    while let Some(error) = cause {
        messages.push(error.to_string());
        cause = error.source();
    }
    messages
}

async fn perform(input: &Value) -> Result<Value, Box<dyn Error + Send + Sync>> {
    let mode = input["mode"].as_str().unwrap_or("request");
    let base = input["api_base"].as_str().ok_or("api_base missing")?;
    let target = input["target"].as_str().ok_or("target missing")?;
    if mode == "classify" {
        return Ok(match api_tls::classify_target(base, target) {
            Ok(matched) => json!({"outcome":"classified", "matched":matched}),
            Err(error) => json!({"outcome":"error", "phase":"classify", "errors":error_details(error.as_ref())}),
        });
    }
    let pem = input["ca_pem"].as_str().ok_or("ca_pem missing")?;
    let client = match api_tls::ApiClient::new(base, pem.as_bytes(), None) {
        Ok(client) => client,
        Err(error) => return Ok(json!({"outcome":"error", "phase":"construct", "errors":error_details(error.as_ref())})),
    };
    let method = reqwest::Method::from_bytes(input["method"].as_str().unwrap_or("GET").as_bytes())?;
    let body = input["body"].as_str();
    let headers = vec![("Content-Type".to_owned(), "application/json".to_owned())];
    let response = match client.request(target, method, headers, body).await {
        Ok(response) => response,
        Err(error) => return Ok(json!({"outcome":"error", "phase":"request", "errors":error_details(error.as_ref())})),
    };
    let status = response.status().as_u16();
    let returned_url = response.url().to_string();
    let bytes = response.bytes().await?;
    if bytes.len() > 8192 {
        return Err("fixture response too large".into());
    }
    Ok(json!({"outcome":"response", "status":status, "url":returned_url,
              "body":String::from_utf8_lossy(&bytes)}))
}

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let source_sha256 = format!("{:x}", Sha256::digest(PRODUCTION_SOURCE));
    let mut text = String::new();
    let result = match io::stdin().take(131072).read_to_string(&mut text) {
        Ok(_) => match serde_json::from_str::<Value>(&text) {
            Ok(input) => perform(&input).await,
            Err(error) => Err(Box::new(error) as Box<dyn Error + Send + Sync>),
        },
        Err(error) => Err(Box::new(error) as Box<dyn Error + Send + Sync>),
    };
    let mut output = result.unwrap_or_else(|error| {
        json!({"outcome":"error", "phase":"harness", "errors":error_details(error.as_ref())})
    });
    output["production_helper_sha256"] = json!(source_sha256);
    output["real_production_helper"] = json!(true);
    output["actualClient"] = json!(false);
    println!("{}", output);
}
