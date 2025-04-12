import streamlit as st
import boto3
import json
from botocore.exceptions import ClientError
from botocore.credentials import Credentials
import pandas as pd
import csv
import re
import os
import uuid
import base64
import tempfile
from datetime import datetime
from io import StringIO

# Constants
BEDROCK_MODEL_ID = "anthropic.claude-3-sonnet-20240229-v1:0"
SUPPORTED_FILE_TYPES = {
    "terraform": [".tf", ".tfvars", ".hcl"],
    "cloudformation": [".yaml", ".yml", ".json"]
}

class Config:
    def __init__(self):
        self.aws_access_key_id = st.secrets["aws_access_key_id"]
        self.aws_secret_access_key = st.secrets["aws_secret_access_key"]
        self.region_name = st.secrets["region"]
        self.workload_id = st.secrets["workload_id"]
        self.s3_bucket = st.secrets["s3_bucket"]
        self.lens_alias = 'wellarchitected'
def validate_terraform_file(file_content):
    """Validate if the file is a valid Terraform configuration"""
    try:
        content = file_content.read().decode('utf-8')
        file_content.seek(0)  # Reset file pointer
        
        # Basic Terraform syntax validation
        if any(keyword in content for keyword in ['resource', 'provider', 'variable', 'terraform']):
            return True
        return False
    except Exception as e:
        st.error(f"Error validating Terraform file: {str(e)}")
        return False

def get_file_type(filename):
    """Determine if the file is Terraform or CloudFormation"""
    extension = os.path.splitext(filename)[1].lower()
    if extension in SUPPORTED_FILE_TYPES["terraform"]:
        return "terraform"
    elif extension in SUPPORTED_FILE_TYPES["cloudformation"]:
        return "cloudformation"
    return None
def upload_file_to_s3(uploaded_file, s3_bucket):
    try:
        file_type = get_file_type(uploaded_file.name)
        if not file_type:
            st.error("Unsupported file type. Please upload a valid Terraform or CloudFormation file.")
            return None

        if file_type == "terraform" and not validate_terraform_file(uploaded_file):
            st.error("Invalid Terraform configuration file.")
            return None

        # Generate a unique path in S3
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        s3_key = f"uploads/{timestamp}/{uploaded_file.name}"
        
        s3_client.upload_fileobj(uploaded_file, s3_bucket, s3_key)
        file_url = f"https://{s3_bucket}.s3.{s3_client.meta.region_name}.amazonaws.com/{s3_key}"
        
        st.success("Your workload received successfully!")
        return {
            'url': file_url,
            'type': file_type
        }
    except ClientError as e:
        st.error(f"Error uploading file to S3: {e}")
        return None
def analyze_template_with_bedrock(file_info, best_practices_json_path):
    model_id = BEDROCK_MODEL_ID
    
    try:
        # Load the best practices JSON
        response = s3_client.get_object(Bucket=s3_bucket, Key=best_practices_json_path)
        content = response['Body'].read().decode('utf-8')
        best_practices = json.loads(content)
        
        # Create appropriate prompt based on file type
        if file_info['type'] == "terraform":
            prompt = create_terraform_prompt(file_info['url'], best_practices)
        else:
            prompt = create_cloudformation_prompt(file_info['url'], best_practices)

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]
        }

        response = bedrock_client.invoke_model(
            modelId=model_id,
            contentType='application/json',
            accept='application/json',
            body=json.dumps(request_body)
        )
        
        response_body = json.loads(response['body'].read())
        analysis_content = response_body.get('content', [])
        
        analysis_result = "\n".join(
            item['text'] for item in analysis_content if item['type'] == 'text'
        )
        
        return analysis_result
    except Exception as e:
        st.error(f"Error analyzing template: {str(e)}")
        return None

def create_terraform_prompt(file_url, best_practices):
    return f"""
    Analyze the following Terraform configuration from URL: {file_url}
    
    For each of the following best practices from the AWS Well-Architected Framework,
    determine if it is applied in the given Terraform configuration.
    
    Best Practices:
    {json.dumps(best_practices, indent=2)}
    
    For each best practice, respond in the following EXACT format only:
    [Exact Best Practice Name as given in Best Practices]: [Why do you consider this best practice applicable?]
    
    IMPORTANT: Use the EXACT best practice name as given in the Best Practices.
    List only the practices which are Applied.
    Consider Terraform-specific implementations and resources.
    """

def create_cloudformation_prompt(file_url, best_practices):
    return f"""
    Analyze the following CloudFormation template from URL: {file_url}
    
    For each of the following best practices from the AWS Well-Architected Framework,
    determine if it is applied in the given CloudFormation template.
    
    Best Practices:
    {json.dumps(best_practices, indent=2)}
    
    For each best practice, respond in the following EXACT format only:
    [Exact Best Practice Name as given in Best Practices]: [Why do you consider this best practice applicable?]
    
    IMPORTANT: Use the EXACT best practice name as given in the Best Practices.
    List only the practices which are Applied.
    """
def main():
    st.title("Are you Well-Architected? ✅")
    
    best_practices_file_path = 'well_architected_best_practices.json'
    best_practices_csv_path = 'well_architected_best_practices.csv'
    
    # Initialize session state variables
    initialize_session_state()
    
    # File upload section with support for both Terraform and CloudFormation
    uploaded_file = st.file_uploader(
        "Upload your Infrastructure as Code (Terraform or CloudFormation)",
        type=[ext[1:] for exts in SUPPORTED_FILE_TYPES.values() for ext in exts]
    )
    
    if uploaded_file is not None:
        file_info = upload_file_to_s3(uploaded_file, s3_bucket)
        
        if file_info:
            col1, col2, col3 = st.columns(3)
            
            with col1:
                analyze_button = st.button(
                    "AWS best practices I'm Using!",
                    key='analyze_button',
                    on_click=analyze_callback,
                    disabled=st.session_state.analyze_disabled
                )
            
            with col2:
                update_button = st.button(
                    "Complete a WA Review",
                    key='update_button',
                    on_click=update_callback,
                    disabled=st.session_state.update_disabled
                )
            
            with col3:
                report_button = st.button(
                    "Show me Detailed Report",
                    key='report_button',
                    disabled=st.session_state.report_disabled
                )
            
            if file_info and analyze_button:
                if st.session_state.analyze_click == 1:
                    with st.spinner('Checking your workloads for AWS best practices...'):
                        analysis_results = analyze_template_with_bedrock(file_info, best_practices_file_path)
                        st.session_state.analyze_click += 1
                        st.session_state.analysis_result = analysis_results
                        
                        if st.session_state.analysis_result:
                            display_result(st.session_state.analysis_result, best_practices_csv_path)
                        else:
                            st.error("Failed to analyze the template. Please try again.")
                            st.session_state.update_disabled = True
                            st.session_state.report_disabled = True
                else:
                    display_result(st.session_state.analysis_result, best_practices_csv_path)
            
            # Rest of the button handling code remains the same
def initialize_session_state():
    """Initialize all session state variables"""
    session_vars = {
        'analysis_result': None,
        'analyze_disabled': False,
        'analyze_click': 1,
        'update_click': 1,
        'report_click': 1,
        'report_link': None,
        'update_disabled': True,
        'report_disabled': True
    }
    
    for var, value in session_vars.items():
        if var not in st.session_state:
            st.session_state[var] = value
